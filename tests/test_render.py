import tempfile
import unittest
from pathlib import Path
import wave

from instagram_pipeline.render import caption_chunks, join_narration, validate_episode, write_subtitles, ScenePainter


def episode():
    return {"title":"Una historia comprobable", "scenes":[
        {"id":"s1","narration":"La historia se cuenta con pruebas y fuentes que podemos consultar.",
         "visual":{"kind":"artifact","title":"La historia en movimiento","label":"Ilustración simbólica"}}
    ]}


class RenderTests(unittest.TestCase):
    def test_subtitle_chunking_preserves_every_word(self):
        text="Un relato breve, con datos comprobados. Cada dato conserva su fuente y ningún detalle se inventa."
        chunks=caption_chunks(text)
        self.assertEqual(" ".join(chunks),text)
        self.assertTrue(all(len(c.split())<=8 for c in chunks))

    def test_audio_join_preserves_samples_and_scene_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            paths=[]
            for i,samples in enumerate((8000,16000)):
                path=root/f"{i}.wav"
                with wave.open(str(path),"wb") as audio:
                    audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(8000)
                    audio.writeframes(b"\x01\x00"*samples)
                paths.append(path)
            timing=join_narration(paths,root/"all.wav",gap=.25)
            self.assertEqual(timing[1]["start"],1.25)
            self.assertEqual(timing[1]["voice_duration"],2.0)
            with wave.open(str(root/"all.wav"),"rb") as result:
                self.assertEqual(result.getnframes(),28000)

    def test_subtitles_do_not_carry_orphaned_sentence_end_to_next_sentence(self):
        chunks=caption_chunks("¿Puede una bola dar la hora a los barcos? En Greenwich encontramos la respuesta.")
        self.assertTrue(chunks[1].endswith("barcos?"))
        self.assertTrue(chunks[2].startswith("En Greenwich"))

    def test_long_visual_text_rejected_before_render(self):
        content=episode()
        content["scenes"][0]["visual"]["label"]="x"*161
        with self.assertRaises(ValueError):
            validate_episode(content)

    def test_animation_changes_pixels_even_with_same_caption(self):
        painter=ScenePainter(episode(),0)
        first=painter.frame(.1,10,.01)
        second=painter.frame(.5,10,.01)
        self.assertEqual(first.size,(540,960))
        self.assertNotEqual(first.crop((34,300,506,638)).tobytes(),second.crop((34,300,506,638)).tobytes())

    def test_srt_timing_stays_inside_spoken_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"subtitles.srt"
            write_subtitles(episode(),[{"start":2,"voice_duration":4,"duration":4.22}],path)
            text=path.read_text(encoding="utf-8")
            self.assertIn("00:00:02,000 -->",text)
            self.assertIn("--> 00:00:06,000",text)


if __name__=="__main__":
    unittest.main()
