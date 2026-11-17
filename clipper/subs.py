"""Word timings for one-word subtitles (local speech-to-text in stt.py) and the SRT writer."""
import json

from . import stt


def words(src, out_json, past=False):
    w = stt.words(src, past=past)
    # clean: drop empties, cap each word's display at 0.9 s so silences stay empty
    out = []
    for x in w:
        txt = x["text"].strip()
        if not txt:
            continue
        out.append({"text": txt, "start": round(x["start"], 3), "end": round(min(x["end"], x["start"] + 0.9), 3)})
    out_json.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def _ts(s):
    s = max(0.0, s)
    h, r = divmod(int(s * 1000), 3600000)
    m, r = divmod(r, 60000)
    sec, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def srt(words_list, timeline, path):
    """timeline: list of (src_start, src_end, out_start) segments, as in the render (hook, then full clip)."""
    cues = []
    for a, b, o in timeline:
        for w in words_list:
            if w["start"] >= a and w["start"] < b:
                s = o + w["start"] - a
                e = o + min(w["end"], b) - a
                cues.append((s, e, w["text"]))
    lines = []
    for i, (s, e, t) in enumerate(cues, 1):
        lines += [str(i), f"{_ts(s)} --> {_ts(e)}", t, ""]
    path.write_text("\n".join(lines), encoding="utf-8")
