"""High-quality speech-to-text worker for past streams: Qwen3-ASR 1.7B + Qwen3-ForcedAligner 0.6B on an NVIDIA GPU.

Runs in its own Python environment (.venv-stt: PyTorch with CUDA, transformers 4.57, qwen-asr), separate from the
dashboard's, because those packages need versions the dashboard can't use. It doesn't import anything from
Clipper. stt.py starts it and talks to it with one JSON object per line:

  in : {"id": 1, "path": "audio or video file", "language": "English"}     (or {"quit": true})
  out: {"ready": true, "device": "...", "free_gb": n}                         once, after the models are loaded
       {"id": 1, "done": seconds, "total": seconds}                           while working
       {"id": 1, "words": [{"text", "start", "end"}, ...]}                   when finished
       {"id": 1, "error": "..."}

Two passes per 20-minute block: the ASR model writes the text, then the aligner times every word. Both models
together (~5.5 GB) don't leave an 8 GB laptop GPU room to work once the desktop and other apps have their share,
and Windows then quietly spills into system RAM and everything crawls. So only the model in use sits on the GPU
(the other waits in RAM) unless both fit, and PyTorch is capped at the memory that's actually free, so running
out shows up as an out-of-memory error (smaller batch, then a clear failure) instead of a crawl.

usage: <.venv-stt python> stt_worker.py <asr model dir> <aligner model dir>"""
import json
import subprocess
import sys
import time
import types

import numpy as np

SR = 16000
PIECE_S = 60            # seconds per piece: ~0.45 GB of working memory each, and faster per second than longer
                        # pieces (the aligner takes at most 180 s)
BLOCK_S = 20 * 60       # read the file 20 minutes at a time (a 6 h stream doesn't have to fit in memory)
BATCH = 2               # pieces per GPU pass (halved when the GPU runs out of memory)
HEADROOM = 1.5 * 2**30  # working memory a pass needs beyond the model's weights
MARGIN = 256 * 2**20    # left free for the desktop
TEXT_SHARE = 0.8        # progress: the text pass is ~80 % of the work, timing words the rest


def gpu_free(torch):
    """Bytes free on the GPU. On Windows (WDDM) PyTorch's mem_get_info reports 0 once a model has been copied in
    while ~2 GB are really free, so nvidia-smi's figure is used when it's there."""
    try:
        out = subprocess.run(["nvidia-smi", "--id=0", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return float(out.splitlines()[0]) * 2**20
    except Exception:
        return float(torch.cuda.mem_get_info()[0])


def say(**msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


class Models:
    """The ASR model and the aligner, moved on and off the GPU as each pass needs it."""

    def __init__(self, asr_dir, aligner_dir):
        sys.modules.setdefault("nagisa", types.ModuleType("nagisa"))     # Japanese word splitter; not used here
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("No NVIDIA GPU (CUDA) available for the high-quality speech-to-text.")
        from qwen_asr import Qwen3ASRModel
        from qwen_asr.inference.qwen3_forced_aligner import Qwen3ForcedAligner
        self.torch = torch
        kw = {"dtype": torch.bfloat16, "device_map": "cpu"}
        self.asr = Qwen3ASRModel.from_pretrained(asr_dir, max_inference_batch_size=BATCH, max_new_tokens=1024, **kw)
        self.aligner = Qwen3ForcedAligner.from_pretrained(aligner_dir, **kw)
        self.clean = self.aligner.aligner_processor.clean_token
        self.device = torch.cuda.get_device_name(0)
        self.use("asr")

    def _size(self, m):
        return sum(p.numel() * p.element_size() for p in m.parameters())

    def use(self, which):
        """Put `which` ("asr" or "aligner") on the GPU, moving the other off when they don't both fit."""
        torch = self.torch
        want, other = (self.asr.model, self.aligner.model) if which == "asr" else (self.aligner.model, self.asr.model)
        if want.device.type == "cuda":
            return
        if other.device.type == "cuda" and gpu_free(torch) < self._size(want) + HEADROOM:
            other.to("cpu")
            torch.cuda.empty_cache()
        want.to("cuda:0")
        self.cap()

    def cap(self):
        """Limit PyTorch to what it holds now plus what's free on the card, so running out raises instead of
        spilling into (very slow) system RAM."""
        torch = self.torch
        total = torch.cuda.get_device_properties(0).total_memory
        limit = torch.cuda.memory_reserved() + gpu_free(torch) - MARGIN
        torch.cuda.set_per_process_memory_fraction(max(0.1, min(1.0, limit / total)))

    def free_gb(self):
        return round(gpu_free(self.torch) / 2**30, 2)

    def run(self, fn, items, batch):
        """fn(list) over items in batches; halves the batch when the GPU runs out of memory. Returns (results,
        batch that worked)."""
        out, i = [], 0
        while i < len(items):
            group = items[i:i + batch]
            try:
                out.extend(fn(group))
            except self.torch.OutOfMemoryError:
                self.torch.cuda.empty_cache()
                if batch == 1:
                    raise RuntimeError(f"The GPU ran out of memory ({self.free_gb()} GB free). Close GPU-heavy apps "
                                       "(DaVinci Resolve, games, OBS) and try again.") from None
                batch = max(1, batch // 2)
                continue
            i += len(group)
        return out, batch


def blocks(path):
    """(start seconds, samples) for each BLOCK_S of the file's audio, 16 kHz mono float32."""
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    t = 0.0
    try:
        while True:
            raw = p.stdout.read(SR * 4 * BLOCK_S)
            if not raw:
                break
            x = np.frombuffer(raw[: len(raw) // 4 * 4], np.float32)
            yield t, x
            t += len(x) / SR
    finally:
        if p.poll() is None:
            p.kill()


def seconds(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def with_punctuation(text, items, clean):
    """The aligner drops punctuation; put each transcript word (with its ? ! , .) back on its timing."""
    toks = [t for t in text.split() if clean(t)]
    if len(toks) == len(items):
        return [(tok, it) for tok, it in zip(toks, items)]
    return [(it.text, it) for it in items]


def transcribe(m: Models, path, language, report):
    from qwen_asr.inference.utils import split_audio_into_chunks
    total = seconds(path) or 1.0
    words, batch_text, batch_align = [], BATCH, BATCH
    for t0, x in blocks(path):
        span = len(x) / SR
        pieces = [(w, t0 + off) for w, off in split_audio_into_chunks(x, SR, PIECE_S)]
        # pass 1: the text of every piece
        m.use("asr")
        done = [0.0]

        def text_of(group):
            m.asr.max_inference_batch_size = len(group)
            res = m.asr.transcribe([(w, SR) for w, _ in group], language=language)
            done[0] += sum(len(w) for w, _ in group) / SR
            report(min(total, t0 + TEXT_SHARE * done[0]), total)
            return [r.text or "" for r in res]
        texts, batch_text = m.run(text_of, pieces, batch_text)
        # pass 2: when each word is said
        m.use("aligner")
        todo = [(w, off, txt) for (w, off), txt in zip(pieces, texts) if txt.strip()]
        done[0] = 0.0

        def align(group):
            res = m.aligner.align(audio=[(w, SR) for w, _, _ in group], text=[t for _, _, t in group],
                                  language=[language] * len(group))
            done[0] += sum(len(w) for w, _, _ in group) / SR
            report(min(total, t0 + TEXT_SHARE * span + (1 - TEXT_SHARE) * done[0]), total)
            return list(res)
        aligned, batch_align = m.run(align, todo, batch_align)
        for (w, off, txt), r in zip(todo, aligned):
            for text, it in with_punctuation(txt, list(r), m.clean):
                if it.end_time >= it.start_time:
                    words.append({"text": text, "start": round(off + it.start_time, 3),
                                  "end": round(off + max(it.end_time, it.start_time + 0.05), 3)})
        report(min(total, t0 + span), total)
    words.sort(key=lambda w: w["start"])
    return words


def main():
    asr_dir, aligner_dir = sys.argv[1], sys.argv[2]
    try:
        models = Models(asr_dir, aligner_dir)
    except Exception as e:
        say(ready=False, error=f"{type(e).__name__}: {e}")
        return 1
    say(ready=True, device=models.device, free_gb=models.free_gb())
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        if req.get("quit"):
            break
        rid = req.get("id")
        try:
            last = [0.0]

            def report(done, total):
                now = time.time()
                if now - last[0] > 0.5 or done >= total:
                    last[0] = now
                    say(id=rid, done=round(done, 1), total=round(total, 1))
            say(id=rid, words=transcribe(models, req["path"], req.get("language") or "English", report))
        except Exception as e:
            say(id=rid, error=f"{type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
