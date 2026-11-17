"""Local speech-to-text for word-timed subtitles: NVIDIA Parakeet TDT 0.6B v3 (int8) through sherpa-onnx.
Runs offline on the CPU. The model (~490 MB download, ~640 MB on disk) is optional: without it, clips get no
subtitles and everything else works. Get it with `clipper stt-download` (install.sh offers it)."""
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from .common import MODELS, TMP, config

NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{NAME}.tar.bz2"
FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
_REC = {}


def model_dir() -> Path:
    d = config().get("stt_model")
    return Path(d).expanduser() if d else MODELS / NAME


def available() -> bool:
    d = model_dir()
    return all((d / f).exists() for f in FILES)


def download(progress=print):
    """Fetch and unpack the model into models/."""
    MODELS.mkdir(parents=True, exist_ok=True)
    TMP.mkdir(parents=True, exist_ok=True)
    arc = TMP / f"{NAME}.tar.bz2"
    req = urllib.request.Request(URL, headers={"User-Agent": "Clipper"})
    with urllib.request.urlopen(req, timeout=60) as r, open(arc, "wb") as f:
        total, got, last = int(r.headers.get("Content-Length") or 0), 0, -1
        while chunk := r.read(1 << 20):
            f.write(chunk)
            got += len(chunk)
            pct = int(got * 100 / total) if total else 0
            if pct != last and pct % 5 == 0:
                progress(f"Speech-to-text model: {pct}% of {total / 1e6:.0f} MB")
                last = pct
    progress("Unpacking")
    with tarfile.open(arc, "r:bz2") as t:
        t.extractall(MODELS, filter="data")
    arc.unlink(missing_ok=True)
    return MODELS / NAME


def _recognizer():
    import sherpa_onnx
    d = str(model_dir())
    if d not in _REC:
        _REC.clear()
        _REC[d] = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=f"{d}/encoder.int8.onnx", decoder=f"{d}/decoder.int8.onnx", joiner=f"{d}/joiner.int8.onnx",
            tokens=f"{d}/tokens.txt", model_type="nemo_transducer", num_threads=4, decoding_method="greedy_search")
    return _REC[d]


def words(path):
    """[{"text", "start", "end"}] in seconds for one audio/video file."""
    import numpy as np
    if not available():
        raise RuntimeError("No speech-to-text model installed. Run `clipper stt-download` to add subtitles.")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    r = _recognizer()
    s = r.create_stream()
    s.accept_waveform(16000, x)
    r.decode_stream(s)
    out, cur, t0, last = [], "", None, 0.0
    for tk, t in zip(list(s.result.tokens), list(s.result.timestamps)):
        if tk.startswith("▁") or tk.startswith(" "):
            if cur:
                out.append({"text": cur, "start": t0, "end": t})
            cur, t0 = tk.lstrip("▁ "), t
        else:
            cur += tk
        last = t
    if cur:
        out.append({"text": cur, "start": t0, "end": last + 0.25})
    return out
