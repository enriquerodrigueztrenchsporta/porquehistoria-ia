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
