import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from instagram_pipeline.runner import quality_gate


class QualityGateTests(unittest.TestCase):
    def test_accepts_manifest_written_by_renderer_with_piper(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / "reel.mp4").write_bytes(b"video")
            manifest = {"duration_seconds": 40, "width": 1080, "height": 1920, "fps": 30,
                        "video_codec": "h264", "audio_codec": "aac", "decode_check_passed": True,
                        # Value returned by render.synthesize() for the Piper engine.
                        "voice_engine": "piper-neural-local",
                        "sha256": hashlib.sha256(b"video").hexdigest()}
            (out / "render-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            episode = {"generation_mode": "local_ai", "verification": {"passed": True}}
            self.assertEqual(quality_gate(episode, out)["voice_engine"], "piper-neural-local")

    def test_rejects_system_voice_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / "reel.mp4").write_bytes(b"video")
            manifest = {"duration_seconds": 40, "width": 1080, "height": 1920, "fps": 30,
                        "video_codec": "h264", "audio_codec": "aac", "decode_check_passed": True,
                        "voice_engine": "windows-sapi-spanish-synthetic",
                        "sha256": hashlib.sha256(b"video").hexdigest()}
            (out / "render-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            episode = {"generation_mode": "local_ai", "verification": {"passed": True}}
            with self.assertRaises(RuntimeError):
                quality_gate(episode, out)


class GenerateFallbackTests(unittest.TestCase):
    def test_blocked_source_is_skipped_without_using_an_ai_attempt(self):
        from unittest.mock import patch
        from instagram_pipeline import runner
        from instagram_pipeline.research import ResearchError

        topics = [{"id": t, "title": t, "sources": []} for t in ("blocked", "b", "c", "d")]

        def research(topic, cache_dir):
            if topic["id"] == "blocked":
                raise ResearchError("Fuente no disponible (HTTP 403)")
            return {"facts": []}

        reviewed = []

        def review(topic, evidence, endpoint, model):
            reviewed.append(topic["id"])
            raise RuntimeError("rechazado por el verificador")

        with tempfile.TemporaryDirectory() as folder, \
                patch.dict("os.environ", {"PIPER_VOICE": __file__}), \
                patch("instagram_pipeline.research.load_topics", return_value=topics), \
                patch("instagram_pipeline.research.research_topic", side_effect=research), \
                patch("instagram_pipeline.agents.generate_episode", side_effect=review):
            with self.assertRaises(RuntimeError):
                runner.generate(Path(folder), [])
        # Two AI attempts, both on reachable topics; the 403 topic never reaches the model.
        self.assertEqual(reviewed, ["b", "c"])
