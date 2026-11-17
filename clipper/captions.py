"""A published stream's own subtitles, fetched first on every platform: speech-to-text (stt.py) only runs when
there are none.

YouTube's auto-captions (json3) carry a time for every word; uploaded subtitles (json3 or WebVTT, any platform
yt-dlp finds them on) time whole lines, and their words are spread over the line. Twitch and Kick VODs normally
have none, so those get transcribed.
"""
import json
import re
import subprocess
from pathlib import Path

from .common import read_json, write_json

_MARK = re.compile(r"^(\[.*\]|>>+|-)$")


def fetch(url, workdir: Path, platform: str, cookies_browser=None):
    """[{"text", "start", "end"}] for the whole stream, or None when the platform has no subtitles for it."""
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
    if not picks and manual:                       # the stream's language isn't given: any uploaded subtitles
        picks = [("--write-subs", k) for k in manual][:2]
    for flag, code in picks[:3]:
        for f in list(workdir.glob("captions.*.json3")) + list(workdir.glob("captions.*.vtt")):
            f.unlink()
        subprocess.run(base + [flag, "--sub-langs", code, "--sub-format", "json3/vtt/best", "-o",
                               str(workdir / "captions"), url], capture_output=True, text=True, timeout=300)
        files = list(workdir.glob("captions.*.json3")) + list(workdir.glob("captions.*.vtt"))
        if files:
            words = parse_json3(files[0]) if files[0].suffix == ".json3" else parse_vtt(files[0])
            for f in files:
                f.unlink()
            if words:
                write_json(cached, words)
                return words
    return None


def _vtt_time(s):
    secs = 0.0
    for x in s.strip().replace(",", ".").split(":"):
        secs = secs * 60 + float(x)
    return secs


def parse_vtt(path):
    """WebVTT cues -> words spread evenly over each cue (repeated rolling lines are dropped)."""
    out, last = [], ""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for block in re.split(r"\n\s*\n", text):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        ti = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if ti is None:
            continue
        a, b = (x.split()[0] for x in lines[ti].split("-->"))
        t0, t1 = _vtt_time(a), _vtt_time(b)
        line = re.sub(r"<[^>]+>", "", " ".join(lines[ti + 1:])).strip()
        if not line or line == last:
            continue
        new = line[len(last):].strip() if last and line.startswith(last) else line
        last = line
        ws = [w for w in new.split() if not _MARK.match(w)]
        for k, w in enumerate(ws):
            out.append({"text": w, "start": round(t0 + (t1 - t0) * k / len(ws), 3),
                        "end": round(t0 + (t1 - t0) * (k + 1) / len(ws), 3)})
    out.sort(key=lambda w: w["start"])
    return out


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
