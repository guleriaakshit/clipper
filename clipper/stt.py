"""Local speech-to-text, offline and free. Two models, picked by what's being transcribed:

- Live streams (and any computer without an NVIDIA GPU): NVIDIA Parakeet TDT 0.6B v2, int8, through sherpa-onnx on
  the CPU. English, word timings built in, many times faster than real time, so it keeps up with a live stream.
  (Parakeet v3, the multilingual one older installs downloaded, still works.) ~480 MB: `clipper stt-download`.

- Past streams, when there's an NVIDIA GPU: Qwen3-ASR 1.7B with Qwen3-ForcedAligner 0.6B for word timings. The most
  accurate local model that fits an 8 GB laptop GPU; nothing has to keep up in real time, so accuracy wins. It runs
  in its own Python environment (.venv-stt, PyTorch with CUDA) as a worker process (stt_worker.py), loaded once per
  analysis and closed afterwards to free the GPU. ~9 GB in all: `clipper stt-hq`.
  If it isn't installed or fails, past streams use Parakeet."""
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

from .common import MODELS, ROOT, TMP, WINDOWS, config

# ---------------- Parakeet (live) ----------------
NAME = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
OLDER = ["sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"]
URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{NAME}.tar.bz2"
FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")
_REC = {}
THREADS = max(4, min(8, (os.cpu_count() or 4) // 2))   # half the cores, 4-8: whole streams take a while


def _has(d: Path) -> bool:
    return all((d / f).exists() for f in FILES)


def model_dir() -> Path:
    d = config().get("stt_model")
    if d and _has(Path(d).expanduser()):
        return Path(d).expanduser()
    for name in [NAME] + OLDER:
        if _has(MODELS / name):
            return MODELS / name
    return MODELS / NAME


def model_name() -> str:
    n = model_dir().name
    return "Parakeet TDT 0.6B v2" if "v2" in n else "Parakeet TDT 0.6B v3" if "v3" in n else n


def available() -> bool:
    return _has(model_dir())


def download(progress=print):
    """Fetch and unpack the live model into models/."""
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
            tokens=f"{d}/tokens.txt", model_type="nemo_transducer", num_threads=THREADS,
            decoding_method="greedy_search")
    return _REC[d]


def words(path, past=False):
    """[{"text", "start", "end"}] in seconds for one audio/video file. past=True: the high-quality GPU model when
    it's installed (Parakeet otherwise)."""
    if past and hq_available():
        try:
            return hq_words(path)
        except Exception as e:                 # fall back to Parakeet; the reason is in tmp/stt_worker.log
            _note_failure(e)
    if not available():
        raise RuntimeError("No speech-to-text model installed. Run `clipper stt-download` to add subtitles.")
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    import numpy as np
    return words_from_array(np.frombuffer(raw[: len(raw) // 4 * 4], np.float32))


def words_from_array(x, offset=0.0):
    """Parakeet, for 16 kHz mono float32 samples; times shifted by offset seconds."""
    if not available():
        raise RuntimeError("No speech-to-text model installed. Run `clipper stt-download` first.")
    if len(x) < 1600:
        return []
    r = _recognizer()
    s = r.create_stream()
    s.accept_waveform(16000, x)
    r.decode_stream(s)
    out, cur, t0, last = [], "", None, 0.0
    for tk, t in zip(list(s.result.tokens), list(s.result.timestamps)):
        if tk.startswith("▁") or tk.startswith(" "):
            if cur:
                out.append({"text": cur, "start": round(offset + t0, 3), "end": round(offset + t, 3)})
            cur, t0 = tk.lstrip("▁ "), t
        else:
            cur += tk
        last = t
    if cur:
        out.append({"text": cur, "start": round(offset + t0, 3), "end": round(offset + last + 0.25, 3)})
    return out


def can_transcribe(past=False) -> bool:
    return available() or (past and hq_available())


# ---------------- Qwen3-ASR on the GPU (past streams) ----------------
HQ_NAME = "Qwen3-ASR 1.7B"
HQ_ENV = ROOT / ".venv-stt"
HQ_ASR = MODELS / "Qwen3-ASR-1.7B"
HQ_ALIGNER = MODELS / "Qwen3-ForcedAligner-0.6B"
HQ_LOG = TMP / "stt_worker.log"
HQ_LANGUAGE = "English"
HQ_PACKAGES = ["transformers==4.57.6", "accelerate==1.12.0", "librosa", "soundfile", "numpy"]
HQ_TORCH_INDEX = "https://download.pytorch.org/whl/cu128"      # CUDA 12.8: also RTX 50-series
_hq = {"proc": None, "failed": None, "lock": threading.Lock(), "n": 0}


def hq_python() -> Path:
    return HQ_ENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def hq_installed() -> bool:
    return hq_python().exists() and (HQ_ASR / "config.json").exists() and (HQ_ALIGNER / "config.json").exists()


def hq_available() -> bool:
    """Installed, not switched off in config.json ("stt_hq": false), and hasn't failed in this run of Clipper."""
    return config().get("stt_hq", True) is not False and hq_installed() and not _hq["failed"]


def _note_failure(e):
    _hq["failed"] = str(e)
    hq_release()


def _start(loading=lambda: None):
    p = _hq["proc"]
    if p and p.poll() is None:
        return p
    loading()
    TMP.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_VERBOSITY="error", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8")
    flags = 0x08000000 if WINDOWS else 0                       # no console window
    p = subprocess.Popen([str(hq_python()), str(Path(__file__).with_name("stt_worker.py")), str(HQ_ASR),
                          str(HQ_ALIGNER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=open(HQ_LOG, "w", encoding="utf-8"), text=True, encoding="utf-8", bufsize=1,
                         env=env, creationflags=flags)
    msg = _read(p)
    if not msg.get("ready"):
        p.kill()
        raise RuntimeError(msg.get("error") or "The speech-to-text worker didn't start (see tmp/stt_worker.log).")
    _hq["proc"] = p
    return p


def _read(p):
    line = p.stdout.readline()
    if not line:
        raise RuntimeError("The speech-to-text worker stopped (see tmp/stt_worker.log).")
    return json.loads(line)


def hq_words(path, progress=lambda done, total: None, loading=lambda: None):
    """Every word in one audio/video file, with the GPU model. progress(done seconds, total seconds) as it goes;
    loading() while the model loads (20-40 s, once per analysis)."""
    with _hq["lock"]:
        p = _start(loading)
        _hq["n"] += 1
        rid = _hq["n"]
        p.stdin.write(json.dumps({"id": rid, "path": str(path), "language": HQ_LANGUAGE}) + "\n")
        p.stdin.flush()
        while True:
            msg = _read(p)
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                raise RuntimeError(msg["error"])
            if "words" in msg:
                return msg["words"]
            progress(msg.get("done", 0), msg.get("total", 0))


def hq_release():
    """Close the worker (frees the GPU). The next hq_words starts it again."""
    p = _hq["proc"]
    _hq["proc"] = None
    if p and p.poll() is None:
        try:
            p.stdin.write(json.dumps({"quit": True}) + "\n")
            p.stdin.flush()
            p.wait(timeout=15)
        except Exception:
            p.kill()


def nvidia_gpu():
    """(name, memory GB) of the first NVIDIA GPU, or None."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    out = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"], capture_output=True,
                         text=True).stdout.strip().splitlines()
    if not out:
        return None
    name, mem = (x.strip() for x in out[0].split(",")[:2])
    return name, round(float(mem) / 1024, 1)


def hq_install(progress=print):
    """Set up the GPU model: .venv-stt (PyTorch with CUDA, transformers, qwen-asr) and the two models (~9 GB)."""
    gpu = nvidia_gpu()
    if not gpu:
        raise RuntimeError("No NVIDIA GPU found (nvidia-smi isn't there), so past streams keep using Parakeet.")
    progress(f"GPU: {gpu[0]}, {gpu[1]} GB")
    if gpu[1] < 5.5:
        progress("  This GPU has under 6 GB; the model may not fit. Clipper falls back to Parakeet if it doesn't.")
    py = hq_python()
    if not py.exists():
        progress("Making the speech-to-text environment (.venv-stt)")
        base = getattr(sys, "_base_executable", None) or sys.executable
        subprocess.run([base, "-m", "venv", str(HQ_ENV)], check=True)
        subprocess.run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], check=True)
    progress("Installing PyTorch with CUDA (~3 GB)")
    subprocess.run([str(py), "-m", "pip", "install", "-q", "torch", "--index-url", HQ_TORCH_INDEX], check=True)
    progress("Installing transformers and Qwen3-ASR")
    subprocess.run([str(py), "-m", "pip", "install", "-q", *HQ_PACKAGES], check=True)
    subprocess.run([str(py), "-m", "pip", "install", "-q", "--no-deps", "qwen-asr==0.0.6"], check=True)
    ok = subprocess.run([str(py), "-c", "import torch; print(torch.cuda.is_available())"], capture_output=True,
                        text=True).stdout.strip()
    if ok != "True":
        raise RuntimeError("PyTorch can't use the GPU (update the NVIDIA driver, then run clipper stt-hq again).")
    for repo, d in (("Qwen/Qwen3-ForcedAligner-0.6B", HQ_ALIGNER), ("Qwen/Qwen3-ASR-1.7B", HQ_ASR)):
        if (d / "config.json").exists():
            continue
        progress(f"Downloading {repo} (this is the big part)")
        subprocess.run([str(py), "-c", "import sys; from huggingface_hub import snapshot_download; "
                        "snapshot_download(sys.argv[1], local_dir=sys.argv[2])", repo, str(d)], check=True)
    progress("Testing it on the GPU")
    t = time.time()
    _hq["failed"] = None
    try:
        _start()
    finally:
        hq_release()
    progress(f"Ready ({time.time() - t:.0f} s to load). Past streams are now transcribed with {HQ_NAME}.")
