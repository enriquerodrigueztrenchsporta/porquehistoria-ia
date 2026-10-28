"""Download the public Spanish Piper model; no Hugging Face account required."""
from pathlib import Path
import shutil
from urllib.request import urlopen

root = Path(__file__).resolve().parent.parent / "models"
root.mkdir(exist_ok=True)
base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/"
for name in ("es_ES-davefx-medium.onnx", "es_ES-davefx-medium.onnx.json", "MODEL_CARD"):
    target = root / name
    if target.exists() and target.stat().st_size > 0:
        continue
    temporary = target.with_suffix(target.suffix + ".download")
    with urlopen(base + name, timeout=90) as response, temporary.open("wb") as output:
        shutil.copyfileobj(response, output)
    temporary.replace(target)
    print("Descargado:", name)
