"""Listen to a whole past stream: download its audio (or, where a platform has no audio-only version, like Kick,
its smallest video), then measure the streamer's voice level and, when needed, transcribe everything on this
computer. Used when "Streamer gets loud" is picked, or "What's said" on a stream with no subtitles of its own.

Results are kept in the job folder (the chart and later runs reuse them):
  signals.json    {"bin", "db"}   loudest 0.5 s per 5 s bin, in dB
  transcript.json [{"text", "start", "end"}] every word, stream seconds (only when transcribed)
  listen.json     what was done, so a second run skips it
The downloaded audio is deleted once it has been read; it can be gigabytes for a long Kick stream."""
import subprocess
import time
from pathlib import Path

import numpy as np

from . import signals, stt
from .common import TMP, read_json, write_json
from .download import _source

SR = signals.SR
CHUNK_S = 30                      # transcribe 30 s at a time, like the live watcher


def _mins(s: float) -> str:
    s = max(0, int(s))
    if s < 60:
        return f"{s} s"
    return f"{s // 3600} h {s % 3600 // 60:02d} min" if s >= 3600 else f"{s // 60} min"


def _size(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cached(jd: Path, speech: bool):
    """(db, words) from an earlier run that covers what's needed, else None."""
    meta = read_json(jd / "listen.json", {}) or {}
    if not meta.get("complete") or (speech and not meta.get("speech")):
        return None
    db = (read_json(jd / "signals.json", {}) or {}).get("db") or []
    words = read_json(jd / "transcript.json", []) if meta.get("speech") else None
    return db, words


def download_audio(url, jd: Path, cookies_browser=None, progress=lambda pct, msg: None) -> Path:
    """The stream's audio-only version when there is one, else its smallest video (audio is the same)."""
    if (jd / "stream_audio.ok").exists():           # downloaded by a run that was stopped while transcribing
        for f in jd.glob("stream_audio.*"):
            if f.suffix not in (".part", ".ytdl", ".ok"):
                return f
    cmd = ["yt-dlp", "--no-warnings", "--newline", "--no-playlist", "-f", "ba/b", "-S", "+size,+br,+res",
           "--concurrent-fragments", "8", "--paths", f"temp:{TMP}", "-o", str(jd / "stream_audio.%(ext)s"),
           "--progress-template", "download:PROG %(progress.downloaded_bytes)s %(progress.total_bytes)s "
           "%(progress.total_bytes_estimate)s %(progress.fragment_index)s %(progress.fragment_count)s "
           "%(progress.speed)s %(progress.eta)s",
           _source(url)]
    if cookies_browser:
        cmd[1:1] = ["--cookies-from-browser", cookies_browser]
    log = open(jd / "listen.log", "w", encoding="utf-8")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, text=True, encoding="utf-8", errors="replace")
    try:
        progress(0, "Starting the download")
        for line in p.stdout:
            if not line.startswith("PROG"):
                continue
            got, total, est, fi, fc, speed, eta = (_num(x) for x in line.split()[1:8])
            whole = total or est
            pct = 100 * got / whole if got and whole else (100 * fi / fc if fi and fc else 0)
            msg = f"{_size(got or 0)}" + (f" of about {_size(whole)}" if whole else "")
            if speed:
                msg += f", {_size(speed)}/s"
            if eta:
                msg += f", about {_mins(eta) if eta >= 60 else f'{int(eta)} s'} left"
            progress(min(99.0, pct), msg)
        p.wait()
    finally:
        if p.poll() is None:
            p.kill()
        log.close()
    if p.returncode != 0:
        lines = [l for l in (jd / "listen.log").read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
        raise RuntimeError("Couldn't download the stream's audio: " + (lines[-1] if lines else "yt-dlp failed"))
    files = [f for f in jd.glob("stream_audio.*") if f.suffix not in (".part", ".ytdl", ".ok")]
    if not files:
        raise RuntimeError("The audio download finished but no file was written.")
    (jd / "stream_audio.ok").touch()
    progress(100, f"Downloaded {_size(files[0].stat().st_size)}")
    return files[0]


def _seconds(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return _num(out) or 0.0


def scan(path: Path, duration: float, speech: bool, progress=lambda pct, msg: None) -> signals.AudioScan:
    """Voice level per 5 s bin and (speech=True) every word, read 30 s at a time."""
    total = _seconds(path) or float(duration or 0) or 1.0
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    sc = signals.AudioScan(speech=speech)
    done, t0, what = 0.0, time.time(), "Transcribed" if speech else "Listened to"
    try:
        progress(0, f"{what} 0 of {_mins(total)}")
        while True:
            chunk = p.stdout.read(SR * 4 * CHUNK_S)
            if not chunk:
                break
            sc.feed(np.frombuffer(chunk[: len(chunk) // 4 * 4], np.float32))
            done += len(chunk) / (SR * 4)
            rate = done / max(0.1, time.time() - t0)                # seconds of stream per second of work
            left = (total - done) / rate if rate > 0 else 0
            msg = f"{what} {_mins(done)} of {_mins(total)}"
            if done >= CHUNK_S * 2 and left > 0:
                msg += f", about {_mins(left) if left >= 60 else f'{int(left)} s'} left ({rate:.0f}x real time)"
            progress(min(99.0, 100 * done / total), msg)
        sc.finish()
        p.wait()
    finally:
        if p.poll() is None:
            p.kill()
    if not sc.db:
        raise RuntimeError("Couldn't read the downloaded audio.")
    progress(100, f"{what} {_mins(done)}" + (f": {len(sc.words):,} words" if speech else ""))
    return sc


def engine() -> str:
    """Which model transcribes a past stream here."""
    return f"{stt.HQ_NAME} on the GPU" if stt.hq_available() else stt.model_name()


def _left(s):
    return _mins(s) if s >= 60 else f"{int(s)} s"


def transcribe_hq(audio: Path, total: float, progress) -> list:
    """Every word with the GPU model; progress(pct, msg) over 0-100."""
    t = {"start": None}

    def report(done, whole):
        whole = whole or total or 1.0
        if t["start"] is None:
            t["start"] = (time.time(), done)
        t0, d0 = t["start"]
        rate = (done - d0) / max(0.1, time.time() - t0)
        msg = f"Transcribed {_mins(done)} of {_mins(whole)} with {stt.HQ_NAME}"
        if rate > 0 and d0 < done < whole:
            msg += f", about {_left((whole - done) / rate)} left ({rate:.0f}x real time)"
        progress(min(99.0, 100 * done / whole), msg)
    progress(0, f"Loading {stt.HQ_NAME} on the GPU (about half a minute)")
    words = stt.hq_words(audio, report)
    progress(100, f"Transcribed {_mins(total)}: {len(words):,} words")
    return words


def listen(url, jd: Path, duration, speech: bool, cookies_browser=None, step=lambda key, pct, msg: None):
    """Download, then read the whole stream. step("audio" | "listen", pct, msg) reports progress (and may raise to
    stop). Returns (db, words or None). speech=True only when the stream has no subtitles of its own."""
    hit = cached(jd, speech)
    if hit:
        step("audio", 100, "Already downloaded and read earlier")
        step("listen", 100, f"Reused the earlier {'transcript' if speech else 'voice levels'}")
        return hit
    if speech and not stt.can_transcribe(past=True):
        raise RuntimeError("Transcribing needs the speech-to-text model. In a terminal run: clipper stt-download")
    audio = download_audio(url, jd, cookies_browser, lambda pct, msg: step("audio", pct, msg))
    model, note, sc = None, "", None
    if speech and stt.hq_available():
        # voice levels first (a quick pass, 0-10 %), then the GPU model transcribes the file (10-100 %)
        sc = scan(audio, duration, False, lambda pct, msg: step("listen", pct * 0.1, f"Measuring voice levels: {msg}"))
        try:
            sc.words = transcribe_hq(audio, _seconds(audio) or duration,
                                     lambda pct, msg: step("listen", 10 + pct * 0.9, msg))
            model = stt.HQ_NAME
        except Exception as e:
            if type(e).__name__ == "Stopped":
                raise
            stt._note_failure(e)
            note = f"The GPU model failed ({e}); transcribed with {stt.model_name()} instead."
    if speech and model is None:
        sc = scan(audio, duration, True, lambda pct, msg: step("listen", pct, f"{msg} ({stt.model_name()})"))
        model = stt.model_name()
    elif sc is None:
        sc = scan(audio, duration, False, lambda pct, msg: step("listen", pct, msg))
    write_json(jd / "signals.json", {"bin": signals.BIN, "db": sc.db})
    if speech:
        write_json(jd / "transcript.json", sc.words)
    write_json(jd / "listen.json", {"complete": True, "speech": speech, "model": model, "note": note,
                                    "seconds": len(sc.db) * signals.BIN, "words": len(sc.words) if speech else 0,
                                    "at": time.strftime("%Y-%m-%d %H:%M")})
    for f in list(jd.glob("stream_audio.*")):
        f.unlink(missing_ok=True)
    return sc.db, (sc.words if speech else None)
