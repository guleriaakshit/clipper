"""A published stream's own auto-generated subtitles (YouTube), so Clipper never has to listen to or transcribe it.

YouTube's auto-captions (json3) carry a time for every word. Twitch and Kick VODs have no subtitles: there,
"what's said" isn't available for the whole stream, and only the short clips themselves get transcribed for
the Short's subtitles (stt.py).
"""
import json
import re
import subprocess
from pathlib import Path

from .common import read_json, write_json

_MARK = re.compile(r"^(\[.*\]|>>+|-)$")


def fetch(url, workdir: Path, platform: str, cookies_browser=None):
    """[{"text", "start", "end"}] for the whole stream, or None when the platform has no subtitles for it."""
    if platform != "youtube":
        return None
    cached = workdir / "captions.json"
    if cached.exists():
        return read_json(cached) or None
    base = ["yt-dlp", "--no-warnings", "--skip-download"]
    if cookies_browser:
        base += ["--cookies-from-browser", cookies_browser]
    j = json.loads(subprocess.run(base + ["-J", url], capture_output=True, text=True, timeout=120).stdout or "{}")
    lang = j.get("language") or "en"
    short = lang.split("-")[0]
    auto = j.get("automatic_captions") or {}
    manual = {k: v for k, v in (j.get("subtitles") or {}).items() if k != "live_chat"}
    # the stream's own language: auto captions first (they time every word), then uploaded subtitles
    picks = [("--write-auto-subs", k) for k in (f"{lang}-orig", f"{short}-orig", lang, short) if k in auto]
    picks += [("--write-subs", k) for k in (lang, short) if k in manual]
    picks += [("--write-subs", k) for k in manual if k.startswith(short)]
    for flag, code in picks[:3]:
        for f in workdir.glob("captions.*.json3"):
            f.unlink()
        subprocess.run(base + [flag, "--sub-langs", code, "--sub-format", "json3", "-o", str(workdir / "captions"), url],
                       capture_output=True, text=True, timeout=300)
        files = list(workdir.glob("captions.*.json3"))
        if files:
            words = parse_json3(files[0])
            for f in files:
                f.unlink()
            if words:
                write_json(cached, words)
                return words
    return None


def parse_json3(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for ev in d.get("events", []):
        t0, dur = ev.get("tStartMs"), ev.get("dDurationMs", 0)
        segs = [s for s in ev.get("segs") or [] if (s.get("utf8") or "").strip()]
        if t0 is None or not segs:
            continue
        starts = [t0 + s.get("tOffsetMs", 0) for s in segs]
        for i, s in enumerate(segs):
            end = starts[i + 1] if i + 1 < len(segs) else t0 + max(dur, starts[i] - t0 + 300)
            ws = [w for w in s["utf8"].split() if not _MARK.match(w)]
            for k, w in enumerate(ws):                          # manual subtitles: a whole line in one seg
                n = len(ws)
                a = starts[i] + (end - starts[i]) * k / n
                b = starts[i] + (end - starts[i]) * (k + 1) / n
                out.append({"text": w, "start": round(a / 1000, 3), "end": round(b / 1000, 3)})
    out.sort(key=lambda w: w["start"])
    return out


def clip_words(words, start, end):
    """Words inside [start, end], with times relative to the clip, ready for words.json."""
    out = []
    for w in words:
        if w["start"] >= start and w["start"] < end:
            s = w["start"] - start
            out.append({"text": w["text"], "start": round(s, 3), "end": round(min(w["end"] - start, s + 0.9), 3)})
    return out
