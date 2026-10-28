"""Local motion graphics, offline narration and H.264/AAC rendering.

Drawn graphics are stylised illustrations, never documentary reconstructions.
Piper provides neural TTS when configured. Windows SAPI is an explicitly
identified non-neural fallback, requiring an installed Spanish voice.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import wave

from PIL import Image, ImageDraw, ImageFont


BASE_WIDTH, BASE_HEIGHT = 540, 960
PAPER = (239, 232, 211)
GOLD = (242, 190, 99)
TEAL = (90, 194, 181)
NAVY = (15, 31, 39)
KINDS = {"timeline", "map", "artifact", "document", "comparison"}
ROOT = Path(__file__).resolve().parents[1]


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    options = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / ("segoeuib.ttf" if bold else "segoeui.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in options:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    raise RuntimeError("No se encuentra una fuente Unicode (Segoe UI o DejaVu Sans).")


def caption_chunks(text: str, max_words: int = 8) -> list[str]:
    """Bound captions by words and punctuation; never invent or drop tokens."""
    if max_words < 1:
        raise ValueError("max_words debe ser positivo")
    words = text.split()
    chunks, current = [], []

    def flush(sentence):
        # Balance a nine-word sentence into 5+4, avoiding an orphaned last word
        # that would otherwise join the beginning of the next sentence.
        groups = math.ceil(len(sentence) / max_words)
        quotient, remainder = divmod(len(sentence), groups)
        cursor = 0
        for group in range(groups):
            length = quotient + (group < remainder)
            chunks.append(" ".join(sentence[cursor:cursor+length]))
            cursor += length

    for word in words:
        current.append(word)
        if re.search(r"[.!?;:][\"”']?$", word):
            flush(current)
            current = []
    if current:
        flush(current)
    return chunks


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines, current = [], ""
    for word in str(text).split():
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = ""
        # Handle exceptionally long tokens without cropping them off-screen.
        for character in word:
            if current and draw.textlength(current + character, font=font) > max_width:
                lines.append(current)
                current = ""
            current += character
    if current:
        lines.append(current)
    return lines


def _fitted(draw, text, *, size, width, max_lines, bold=False):
    for candidate in range(size, 15, -1):
        font = _font(candidate, bold)
        lines = _wrap(draw, text, font, width)
        if len(lines) <= max_lines:
            return font, lines
    raise ValueError("Texto demasiado largo para el área segura de vídeo")


def validate_episode(episode: dict) -> None:
    if not isinstance(episode, dict) or not isinstance(episode.get("title"), str):
        raise ValueError("El episodio necesita un título")
    scenes = episode.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 10:
        raise ValueError("Se requieren entre 1 y 10 escenas")
    if not episode.get("title", "").strip() or len(episode["title"]) > 140:
        raise ValueError("Título vacío o demasiado largo")
    for scene in scenes:
        narration = scene.get("narration")
        if not isinstance(narration, str) or not narration.strip() or len(narration) > 1400:
            raise ValueError("Narración vacía o demasiado larga")
        visual = scene.get("visual", {})
        if not isinstance(visual, dict):
            raise ValueError("Visual debe ser un objeto")
        for field, limit in (("title", 110), ("label", 160)):
            if not isinstance(visual.get(field, ""), str) or len(visual.get(field, "")) > limit:
                raise ValueError(f"visual.{field} inválido")


def synthesize(text: str, target: Path, voice_model: Path | None = None) -> str:
    """Write mono PCM WAV. No cloud services, login, or implicit paid fallback."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if voice_model is not None:
        if not voice_model.is_file():
            raise FileNotFoundError(f"Modelo Piper no encontrado: {voice_model}")
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise RuntimeError("Instala piper-tts para utilizar la voz neuronal configurada") from exc
        voice = PiperVoice.load(str(voice_model))
        with wave.open(str(target), "wb") as audio:
            voice.synthesize_wav(text, audio)
        return "piper-neural-local"
    if sys.platform != "win32":
        raise RuntimeError("Configura voice_model de Piper fuera de Windows")
    input_file = target.with_suffix(".txt")
    input_file.write_text(text, encoding="utf-8")
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
         str(ROOT / "scripts" / "speak.ps1"), "-InputFile", str(input_file.resolve()),
         "-OutputFile", str(target.resolve())],
        capture_output=True, timeout=180,
    )
    if proc.returncode:
        raise RuntimeError("Falló la voz local SAPI: " + proc.stderr.decode("utf-8", errors="replace")[-1200:])
    return "windows-sapi-spanish-synthetic"


def join_narration(paths: list[Path], target: Path, gap: float = .22) -> list[dict]:
    timeline, cursor, parameters = [], 0.0, None
    with wave.open(str(target), "wb") as out:
        for source in paths:
            with wave.open(str(source), "rb") as audio:
                current = (audio.getnchannels(), audio.getsampwidth(), audio.getframerate())
                if current[0] != 1 or current[1] != 2:
                    raise ValueError("La narración debe ser PCM mono de 16 bits")
                if parameters is None:
                    parameters = current
                    out.setnchannels(current[0])
                    out.setsampwidth(current[1])
                    out.setframerate(current[2])
                elif current != parameters:
                    raise ValueError("Las voces de las escenas usan formatos de audio distintos")
                voice_duration = audio.getnframes() / current[2]
                if not .2 <= voice_duration <= 90:
                    raise ValueError("Duración de narración de escena fuera de límites")
                out.writeframes(audio.readframes(audio.getnframes()))
                padding_frames = round(gap * current[2])
                out.writeframes(b"\x00" * padding_frames * current[1])
                duration = voice_duration + padding_frames / current[2]
                timeline.append({"start": cursor, "duration": duration, "voice_duration": voice_duration})
                cursor += duration
    return timeline


def _center(draw, text, y, font, fill=PAPER):
    draw.text(((BASE_WIDTH - draw.textlength(text, font=font)) / 2, y), text, font=font, fill=fill)


def _gear(draw, center, radius, rotation, color):
    cx, cy = center
    points = []
    for i in range(64):
        theta = i * math.tau / 64 + rotation
        r = radius if i % 8 in (1, 2, 3, 4) else radius * .84
        points.append((cx + math.cos(theta) * r, cy + math.sin(theta) * r))
    draw.polygon(points, fill=color)
    draw.ellipse((cx-radius*.61,cy-radius*.61,cx+radius*.61,cy+radius*.61), fill=NAVY)
    for i in range(6):
        a = rotation + i*math.tau/6
        draw.line((cx,cy,cx+math.cos(a)*radius*.7,cy+math.sin(a)*radius*.7),fill=color,width=9)
    draw.ellipse((cx-12,cy-12,cx+12,cy+12),fill=color)


class ScenePainter:
    """Compose a reusable scene and animate illustrated objects every frame."""
    def __init__(self, episode: dict, scene_index: int):
        self.episode, self.index = episode, scene_index
        self.scene = episode["scenes"][scene_index]
        self.visual = self.scene.get("visual", {})
        self.kind = self.visual.get("kind", "artifact")
        self.chunks = caption_chunks(self.scene["narration"])
        self.chunk_weights = [max(1, len(chunk)) for chunk in self.chunks]
        self.font_small = _font(15, True)
        self.font_label = _font(18)
        self.font_sub = _font(26, True)
        self.base = Image.new("RGB", (BASE_WIDTH, BASE_HEIGHT), NAVY)
        d = ImageDraw.Draw(self.base)
        for y in range(BASE_HEIGHT):
            strength = math.sin(y / BASE_HEIGHT * math.pi)
            d.line((0, y, BASE_WIDTH, y), fill=(15 + round(4*strength), 31+round(9*strength), 39+round(9*strength)))
        for x in range(20, BASE_WIDTH, 28):
            for y in range(24, BASE_HEIGHT, 28):
                d.ellipse((x,y,x+1,y+1), fill=(35,53,58))
        d.rounded_rectangle((34,78,208,108), radius=15, fill=(34,58,61))
        d.text((47,84), "ARCHIVO DE CURIOSIDADES", font=_font(10,True), fill=TEAL)
        d.text((430,84), f"{scene_index+1:02}/{len(episode['scenes']):02}", font=self.font_small, fill=GOLD)
        title = self.visual.get("title") or episode["title"]
        font, lines = _fitted(d, title, size=37, width=442, max_lines=3, bold=True)
        for j,line in enumerate(lines):
            d.text((40,131+j*(font.size+5)),line,font=font,fill=PAPER)
        d.rounded_rectangle((34,300,506,638),radius=24,fill=(18,38,46),outline=(57,77,79),width=1)
        d.text((53,318), "ILUSTRACIÓN ANIMADA",font=_font(10,True),fill=(135,160,158))
        label = self.visual.get("label", "")
        if label:
            font, lines = _fitted(d, label, size=19, width=400,max_lines=2)
            for j,line in enumerate(lines):
                _center(d,line,575+j*(font.size+3),font,TEAL)
        d.text((42,849), "FUENTES EN LA DESCRIPCIÓN",font=_font(11,True),fill=(129,151,149))
        d.text((42,870), "Animación y narración sintética",font=_font(11),fill=(109,135,134))
        self.caption_cache = {}
        for chunk in self.chunks:
            caption = Image.new("RGBA", (BASE_WIDTH,170),(0,0,0,0))
            cd = ImageDraw.Draw(caption)
            font,lines = _fitted(cd,chunk,size=27,width=430,max_lines=3,bold=True)
            line_height = font.size+8
            start = (155 - len(lines)*line_height)//2
            for j,line in enumerate(lines):
                _center(cd,line,start+j*line_height,font)
            self.caption_cache[chunk] = caption

    def _object(self, d, progress, t):
        cy = 459
        if self.episode.get("topic_id") == "greenwich_time_ball" and self.kind in {"artifact", "comparison"}:
            # Symbolic time ball, not an architectural reconstruction of Greenwich.
            # The falling motion is the sourced fact illustrated in this episode.
            d.rounded_rectangle((186,522,354,548),radius=4,fill=(62,89,91))
            d.rectangle((218,499,322,523),fill=(89,121,121))
            d.line((270,357,270,500),fill=PAPER,width=7)
            fall = min(1, max(0, (progress-.50)/.20))
            ball_y = 390 + 83*fall*fall
            d.ellipse((240,ball_y-30,300,ball_y+30),fill=GOLD)
            d.arc((247,ball_y-23,293,ball_y+23),200,280,fill=(255,222,158),width=4)
            d.arc((319,379,378,501),-65,65,fill=TEAL,width=3)
            d.polygon(((369,476),(382,484),(371,492)),fill=TEAL)
            for j in range(3):
                x=148+j*40
                y=549+3*math.sin(t*1.7+j)
                d.arc((x,y,x+34,y+13),0,180,fill=(59,100,107),width=2)
        elif self.kind == "timeline":
            d.line((85,cy,455,cy),fill=(72,92,94),width=4)
            d.line((85,cy,85+370*progress,cy),fill=GOLD,width=5)
            for i in range(5):
                x=85+i*92.5
                color=GOLD if progress >= i/4 else (65,85,87)
                radius=8 + (3*math.sin(t*3+i) if progress >= i/4 else 0)
                d.ellipse((x-radius,cy-radius,x+radius,cy+radius),fill=color)
                d.line((x,cy+19,x,cy+47),fill=(60,89,91),width=2)
            d.arc((191,362,349,520),185,355,fill=TEAL,width=3)
            angle=t*.5
            d.line((270,432,270+47*math.sin(angle),432-47*math.cos(angle)),fill=TEAL,width=4)
            d.ellipse((262,424,278,440),fill=TEAL)
        elif self.kind == "map":
            # Abstract globe: no invented borders, routes or historical locations.
            d.ellipse((177,363,363,549),outline=TEAL,width=3)
            d.ellipse((224,363,316,549),outline=(73,125,125),width=2)
            d.ellipse((178,416,362,496),outline=(73,125,125),width=2)
            d.line((178,456,362,456),fill=(73,125,125),width=2)
            d.line((270,365,270,548),fill=(73,125,125),width=2)
            d.arc((130,396,411,517),200,345,fill=GOLD,width=3)
            a=t*.65
            x,y=270+135*math.cos(a),456+59*math.sin(a)
            d.ellipse((x-7,y-7,x+7,y+7),fill=GOLD)
        elif self.kind == "document":
            bob=math.sin(t*1.4)*4
            d.rounded_rectangle((177,356+bob,363,546+bob),radius=5,fill=PAPER)
            d.polygon(((326,356+bob),(363,392+bob),(326,392+bob)),fill=(202,191,164))
            for j in range(6):
                length=120 if j%2==0 else 92
                reveal=min(1,max(0,progress*2.4-j*.16))
                d.line((201,400+j*20+bob,201+length*reveal,400+j*20+bob),fill=(78,103,101),width=4)
            d.ellipse((313,505+bob,356,548+bob),fill=(171,88,64))
            d.ellipse((322,514+bob,347,539+bob),outline=GOLD,width=2)
        elif self.kind == "comparison":
            d.line((270,377,270,530),fill=(60,82,86),width=2)
            for cx,rot,color in ((175,t*.35,GOLD),(366,-t*.3,TEAL)):
                radius=54 + 3*math.sin(t+cx)
                _gear(d,(cx,453),radius,rot,color)
            d.line((245,456,295,456),fill=PAPER,width=3)
            d.line((245,465,295,465),fill=PAPER,width=3)
        else:
            _gear(d,(242,445),80,t*.24,GOLD)
            _gear(d,(345,500),46,-t*.42,TEAL)
            d.arc((137,340,364,567),210+progress*40,310+progress*40,fill=(85,118,119),width=2)
            d.ellipse((133,493,145,505),fill=TEAL)

    def frame(self, local_time: float, voice_duration: float, total_progress: float) -> Image.Image:
        im=self.base.copy()
        d=ImageDraw.Draw(im)
        progress=min(1,max(0,local_time/max(voice_duration,.01)))
        self._object(d,progress,local_time)
        weight_position=progress*sum(self.chunk_weights)
        running=0
        selected=self.chunks[-1]
        for chunk,weight in zip(self.chunks,self.chunk_weights):
            running+=weight
            if weight_position < running:
                selected=chunk
                break
        im.paste(self.caption_cache[selected],(0,652),self.caption_cache[selected])
        d=ImageDraw.Draw(im)
        d.rounded_rectangle((42,826,484,829),radius=1,fill=(55,77,81))
        d.rounded_rectangle((42,826,42+442*max(.002,min(1,total_progress)),829),radius=1,fill=GOLD)
        return im


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def inspect_media(path: Path) -> dict:
    """Read the encoded streams, rather than trusting encoder arguments."""
    result = subprocess.run([_ffmpeg(), "-hide_banner", "-i", str(path)],
                            capture_output=True, timeout=30)
    description = result.stderr.decode("utf-8", errors="replace")
    video = next((line for line in description.splitlines() if "Video:" in line), "")
    audio = next((line for line in description.splitlines() if "Audio:" in line), "")
    size = re.search(r"\b(\d{3,5})x(\d{3,5})\b", video)
    rate = re.search(r"([\d.]+) fps", video)
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", description)
    sample_rate = re.search(r"(\d+) Hz", audio)
    if not all((size, rate, duration, sample_rate)):
        raise RuntimeError("No se pueden verificar los streams del MP4 exportado")
    return {"width": int(size[1]), "height": int(size[2]), "fps": float(rate[1]),
            "duration_seconds": int(duration[1])*3600+int(duration[2])*60+float(duration[3]),
            "video_codec": "h264" if "Video: h264" in video else "unknown",
            "pixel_format": "yuv420p" if "yuv420p" in video else "unknown",
            "audio_codec": "aac" if "Audio: aac" in audio else "unknown",
            "audio_sample_rate": int(sample_rate[1])}


def _srt_timestamp(value: float) -> str:
    ms=round(value*1000)
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"


def write_subtitles(episode: dict, timeline: list[dict], target: Path) -> None:
    blocks=[]
    count=0
    for scene,timing in zip(episode["scenes"],timeline):
        chunks=caption_chunks(scene["narration"])
        denominator=sum(len(c) for c in chunks)
        cursor=timing["start"]
        for chunk in chunks:
            count+=1
            end=cursor+timing["voice_duration"]*len(chunk)/denominator
            blocks.append(f"{count}\n{_srt_timestamp(cursor)} --> {_srt_timestamp(end)}\n{chunk}\n")
            cursor=end
    target.write_text("\n".join(blocks),encoding="utf-8")


def render_episode(episode: dict, output_dir: Path, width: int = 1080,
                   height: int = 1920, fps: int = 30,
                   voice_model: Path | None = None) -> Path:
    validate_episode(episode)
    if width%2 or height%2 or width*16 != height*9 or width<270:
        raise ValueError("La resolución debe ser vertical 9:16 con dimensiones pares")
    if not 24 <= fps <= 60:
        raise ValueError("Se requieren entre 24 y 60 fps")
    output_dir=Path(output_dir).resolve()
    output_dir.mkdir(parents=True,exist_ok=True)
    paths=[]
    voice_engine=None
    for i,scene in enumerate(episode["scenes"]):
        target=output_dir/f"voice-{i+1:02}.wav"
        voice_engine=synthesize(scene["narration"],target,voice_model)
        paths.append(target)
    timeline=join_narration(paths,output_dir/"narration.wav")
    duration=sum(t["duration"] for t in timeline)
    if not 3 <= duration <= 90:
        raise ValueError(f"La duración ({duration:.1f}s) queda fuera de 3–90 segundos")
    frames=math.ceil(duration*fps)
    actual_duration=frames/fps
    destination=output_dir/"reel.mp4"
    pending=output_dir/"reel.pending.mp4"
    ffmpeg=_ffmpeg()
    # Draw at 540×960 and scale with Lanczos in FFmpeg: crisp vector-like motion
    # graphics at production output size without unnecessary Python pixel copies.
    command=[ffmpeg,"-hide_banner","-loglevel","error","-y",
             "-f","rawvideo","-pix_fmt","rgb24","-s",f"{BASE_WIDTH}x{BASE_HEIGHT}",
             "-r",str(fps),"-i","-","-i",str(output_dir/"narration.wav"),
             "-vf",f"scale={width}:{height}:flags=lanczos",
             "-c:v","libx264","-preset","fast","-crf","19","-pix_fmt","yuv420p",
             "-c:a","aac","-b:a","128k","-ar","48000","-ac","2",
             "-af","apad","-t",str(actual_duration),"-movflags","+faststart",str(pending)]
    log_path=output_dir/"render.log"
    with log_path.open("wb") as log:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=log,stdout=subprocess.DEVNULL)
        try:
            scene_index=-1
            painter=None
            for frame_index in range(frames):
                seconds=frame_index/fps
                new_index=next((j for j,t in enumerate(timeline) if seconds<t["start"]+t["duration"]),len(timeline)-1)
                if new_index != scene_index:
                    scene_index=new_index
                    painter=ScenePainter(episode,scene_index)
                timing=timeline[scene_index]
                frame=painter.frame(seconds-timing["start"],timing["voice_duration"],seconds/duration)
                if frame_index==min(frames-1,fps*2):
                    frame.resize((width,height),Image.Resampling.LANCZOS).save(output_dir/"preview.png")
                process.stdin.write(frame.tobytes())
            process.stdin.close()
            code=process.wait(timeout=180)
            if code:
                raise RuntimeError(f"FFmpeg falló ({code}). Revisa {log_path}")
        except BaseException:
            process.kill()
            process.wait()
            pending.unlink(missing_ok=True)
            raise
    # Decode the complete result before marking it usable; catch damaged streams.
    check=subprocess.run([ffmpeg,"-hide_banner","-loglevel","error","-i",str(pending),
                          "-f","null","-"],capture_output=True,timeout=180)
    if check.returncode:
        pending.unlink(missing_ok=True)
        raise RuntimeError("El vídeo no supera la comprobación de decodificación")
    observed = inspect_media(pending)
    expected = {"width": width, "height": height, "fps": fps, "video_codec": "h264",
                "pixel_format": "yuv420p", "audio_codec": "aac", "audio_sample_rate": 48000}
    if any(observed[key] != value for key, value in expected.items()):
        pending.unlink(missing_ok=True)
        raise RuntimeError("El vídeo exportado no coincide con el formato técnico requerido")
    if abs(observed["duration_seconds"] - actual_duration) > .15:
        pending.unlink(missing_ok=True)
        raise RuntimeError("La duración exportada no coincide con la línea de tiempo")
    pending.replace(destination)
    write_subtitles(episode,timeline,output_dir/"subtitles.srt")
    manifest={"file":destination.name,"sha256":hashlib.sha256(destination.read_bytes()).hexdigest(),
              "duration_seconds":actual_duration,"width":width,"height":height,"fps":fps,
              "frames":frames,"video_codec":"h264","pixel_format":"yuv420p","audio_codec":"aac",
              "audio_sample_rate":48000,"faststart":True,"voice_engine":voice_engine,
              "animation":"local-procedural-2d","illustrations":"stylised-not-historical-reconstructions",
              "caption_alignment":"proportional-within-each-scene-not-word-forced-alignment",
              "encoded_streams":observed,
              "decode_check_passed":True,"timeline":timeline,
              "source_topic":episode.get("topic_id"),"editorial_verified":bool(episode.get("verification",{}).get("passed"))}
    (output_dir/"render-manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    return destination
