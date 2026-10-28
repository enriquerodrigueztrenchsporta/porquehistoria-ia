"""CLI coordinator: evidence -> AI roles -> animation -> QA -> timed publication."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from urllib.request import urlopen

from .schedule import now_madrid, target_for, wait_for_publication
from .state import Ledger, atomic_json

ROOT = Path(__file__).resolve().parent.parent


def caption_for(episode):
    parts = [episode["title"], "Animación y narración creadas automáticamente con IA. Ilustraciones esquemáticas.", "Fuentes:"]
    parts.extend(f"• {s['title']}: {s['url']}" for s in episode["sources"])
    parts.append("#Historia #CuriosidadesHistoricas #PorQueHistoria")
    caption = "\n\n".join(parts)
    if len(caption) > 2200:
        raise RuntimeError("El pie con fuentes supera el límite de Instagram.")
    return caption


def quality_gate(episode, output_dir):
    if episode.get("generation_mode") != "local_ai" or episode.get("verification", {}).get("passed") is not True:
        raise RuntimeError("Solo se publican episodios generados por IA con auditoría superada.")
    manifest = json.loads((output_dir / "render-manifest.json").read_text(encoding="utf-8"))
    checks = {
        "duration": 35 <= manifest["duration_seconds"] <= 50,
        "size": manifest["width"] == 1080 and manifest["height"] == 1920,
        "fps": manifest["fps"] == 30,
        "codec": manifest["video_codec"] == "h264" and manifest["audio_codec"] == "aac",
        "decoded": manifest["decode_check_passed"] is True,
        "voice": manifest["voice_engine"] == "piper-neural-local",
        "integrity": hashlib.sha256((output_dir / "reel.mp4").read_bytes()).hexdigest() == manifest["sha256"],
    }
    if not all(checks.values()):
        raise RuntimeError("Revisión técnica rechazada: " + ", ".join(k for k, v in checks.items() if not v))
    return manifest


def generate(output_dir, used_topics):
    from .agents import generate_episode
    from .research import ResearchError, discover_topic, load_topics, research_topic
    from .render import render_episode

    model = os.environ.get("OLLAMA_MODEL") or "qwen2.5:7b"
    endpoint = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    voice = Path(os.environ.get("PIPER_VOICE", str(ROOT / "models/es_ES-davefx-medium.onnx")))
    if not voice.is_file():
        raise RuntimeError("Falta el modelo neuronal de voz Piper; ejecuta scripts/download_voice.py.")
    used_ids = {t["id"] for t in used_topics}
    candidates = [t for t in load_topics(ROOT / "data/topics.json")
                  if t["id"] not in used_ids and not t.get("disabled")]
    errors = []
    # Up to two AI attempts (a review failure moves on to a different topic). A source that is
    # unreachable or blocked fails in research, before any model call, so it is skipped cheaply.
    queue = iter(candidates[:8])
    ai_attempts = 0
    while ai_attempts < 2:
        ai_attempts += 1
        try:
            topic = next(queue, None)
            while topic is not None:
                try:
                    print(f"Investigación: {topic['title']}", flush=True)
                    evidence = research_topic(topic, ROOT / ".runtime/sources")
                    break
                except ResearchError as exc:
                    errors.append(f"{topic['id']}: {exc}")
                    print(errors[-1], flush=True)
                    topic = next(queue, None)
            if topic is None:
                topic = discover_topic(ollama_url=endpoint, model=model, used_topics=used_topics,
                                       cache_dir=ROOT / ".runtime/sources", max_articles=3)
                evidence = research_topic(topic, ROOT / ".runtime/sources")
            print(f"Revisión con agentes: {topic['title']}", flush=True)
            episode = generate_episode(topic, evidence, endpoint, model)
            output_dir.mkdir(parents=True, exist_ok=True)
            atomic_json(output_dir / "evidence.json", evidence)
            atomic_json(output_dir / "episode.json", episode)
            print("Animación, voz neuronal y subtítulos", flush=True)
            render_episode(episode, output_dir, voice_model=voice)
            manifest = quality_gate(episode, output_dir)
            caption = caption_for(episode)
            (output_dir / "caption.txt").write_text(caption, encoding="utf-8")
            return topic, episode, manifest, caption
        except (RuntimeError, ValueError, KeyError, OSError) as exc:
            errors.append(f"Intento {ai_attempts}: {exc}")
            print(errors[-1], flush=True)
    raise RuntimeError("No hay vídeo verificable y válido. " + " | ".join(errors))


def daily(preview=False):
    from .instagram import InstagramConfig, InstagramPublisher

    today = now_madrid().date()
    day = today.isoformat()
    output_dir = ROOT / "artifacts" / day
    if not preview and os.environ.get("PUBLISH_ENABLED", "false").lower() != "true":
        raise RuntimeError("Publicación desactivada. Faltan la conexión de Meta y PUBLISH_ENABLED=true.")
    if not preview and now_madrid() > target_for(today) + timedelta(minutes=45):
        raise RuntimeError("Ya pasó la ventana de hoy (20:00–20:45 Madrid). No se publicará tarde.")

    repository = os.environ.get("GITHUB_REPOSITORY") if not preview else None
    ledger = Ledger(ROOT / ".runtime/publication-state.json", repository,
                    os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REF_NAME", "main"))
    if preview:
        generate(output_dir, [])
        print(f"Vista previa completa, sin publicación: {output_dir}")
        return

    config = InstagramConfig(user_id=os.environ.get("INSTAGRAM_USER_ID", ""),
                             access_token=os.environ.get("INSTAGRAM_ACCESS_TOKEN", ""),
                             expected_username=os.environ.get("IG_EXPECTED_USERNAME") or "porquehistoria",
                             api_version=os.environ.get("IG_API_VERSION") or "v25.0",
                             login_mode="facebook")
    record = ledger.data["days"].setdefault(day, {})
    if record.get("journal", {}).get("state") == "published":
        print("El Reel de hoy ya está publicado. No se repite.")
        return
    journal_path = ROOT / ".runtime" / f"instagram-{day}.json"
    key = f"{config.user_id}:{day}"

    def checkpoint(journal):
        record["journal"] = dict(journal)
        record["updated_at"] = now_madrid().isoformat()
        ledger.save()  # CAS failure aborts BEFORE subsequent Meta mutation.

    publisher = InstagramPublisher(config, checkpoint=checkpoint)
    if record.get("journal"):
        atomic_json(journal_path, record["journal"])
        if record["journal"]["state"] not in {"ready", "publish_requested", "published"}:
            raise RuntimeError("Hay una subida interrumpida en el diario. Se requiere revisar su estado antes de sustituirla.")
    else:
        publisher.verify_account()
        topic, episode, manifest, caption = generate(output_dir, ledger.data["used_topics"])
        record.update(topic_id=topic["id"], title=topic["title"],
                      video_sha256=manifest["sha256"],
                      sources=[s["url"] for s in episode["sources"]],
                      duration_seconds=manifest["duration_seconds"])
        # Reserve the topic durably before any upload; retries cannot reuse it next day.
        ledger.data["used_topics"].append({"id": topic["id"], "title": topic["title"],
                                           "sources": [{"url": s["url"]} for s in topic["sources"]]})
        ledger.save()
        publisher.prepare_reel(output_dir / "reel.mp4", caption, journal_path, key)

    print("Vídeo preparado; publicación prevista a las 20:00 Europe/Madrid.", flush=True)
    wait_for_publication(today)
    result = publisher.publish_prepared(journal_path, key)
    print(f"Publicación confirmada: {result.get('media_id', 'reconciliada por Meta')}", flush=True)


def doctor():
    dependencies = {m: bool(importlib.util.find_spec(m)) for m in ("PIL", "imageio_ffmpeg", "piper", "tzdata")}
    try:
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=3) as response:
            models = [m["name"] for m in json.load(response).get("models", [])]
    except OSError:
        models = []
    result = {"dependencies": dependencies, "ollama_models": models,
              "voice_downloaded": (ROOT / "models/es_ES-davefx-medium.onnx").exists(),
              "instagram_user_id_present": bool(os.environ.get("INSTAGRAM_USER_ID")),
              "instagram_token_present": bool(os.environ.get("INSTAGRAM_ACCESS_TOKEN")),
              "publishing_enabled": os.environ.get("PUBLISH_ENABLED") == "true",
              "timezone": "Europe/Madrid", "publication_time": "20:00"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


def check_sources():
    """Fetch every catalogue source from this machine and re-check every literal quote."""
    from .research import load_topics, research_topic
    failed = []
    # Topics marked "disabled" keep their research but are never fetched or published.
    topics = [t for t in load_topics(ROOT / "data/topics.json") if not t.get("disabled")]
    for topic in topics:
        try:
            research_topic(topic, ROOT / ".runtime/sources", refresh=True)
            print(f"OK    {topic['id']}", flush=True)
        except (RuntimeError, OSError) as exc:
            failed.append(topic["id"])
            print(f"FALLO {topic['id']}: {exc}", flush=True)
    print(f"{len(topics) - len(failed)}/{len(topics)} temas accesibles y con citas literales vigentes.")
    if failed:
        raise RuntimeError("Fuentes inaccesibles o modificadas: " + ", ".join(failed))


def main():
    parser = argparse.ArgumentParser(description="PorQueHistoria: animación histórica basada en evidencia")
    parser.add_argument("command", choices=("doctor", "demo", "daily", "check-sources"))
    parser.add_argument("--preview", action="store_true", help="Generar con IA y revisar, sin publicar")
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            doctor()
        elif args.command == "check-sources":
            check_sources()
        elif args.command == "demo":
            from .render import render_episode
            episode = json.loads((ROOT / "data/demo_episode.json").read_text(encoding="utf-8"))
            voice = ROOT / "models/es_ES-davefx-medium.onnx"
            path = render_episode(episode, ROOT / "artifacts/demo", width=540, height=960,
                                  voice_model=voice if voice.exists() else None)
            print(path)
        else:
            daily(args.preview)
    except (RuntimeError, ValueError, KeyError, OSError) as exc:
        # All HTTP credential-bearing modules expose sanitized errors only.
        print(f"Flujo detenido: {exc}", file=sys.stderr)
        sys.exit(1)
