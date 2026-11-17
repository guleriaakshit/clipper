"""analyze <url>: chat -> spikes -> download only the spike parts -> hook, words, face crop."""
import traceback
from pathlib import Path

from . import chat, download, face, hotspot, render, signals, spikes, stt, subs
from .common import job_dir, read_json, set_status, write_json, fmt_t
from .platforms import detect


def analyze(url, top_n=10, cookies_browser=None, chat_file=None, skip_download=False, basis=("chat",), keywords=""):
    """basis: any of "chat", "audio", "speech" (see signals.py)."""
    platform, vid = detect(url)
    jd = job_dir(platform, vid)
    basis = [b for b in ("chat", "audio", "speech") if b in (basis or ())] or ["chat"]
    write_json(jd / "job.json", {"url": url, "platform": platform, "id": vid, "cookies_browser": cookies_browser,
                                 "basis": basis, "keywords": keywords})
    try:
        _run(url, platform, vid, jd, top_n, cookies_browser, chat_file, skip_download, basis, keywords)
    except Exception as e:
        set_status(jd, "error", 0, "", f"{e}")
        (jd / "error.log").write_text(traceback.format_exc())
        raise
    return jd


def _run(url, platform, vid, jd: Path, top_n, cookies_browser, chat_file, skip_download, basis, keywords):
    prog = lambda pct, msg: set_status(jd, "chat", pct, msg)
    msgs = []
    if (jd / "chat.json").exists() and not chat_file:
        msgs, meta = read_json(jd / "chat.json"), read_json(jd / "meta.json")
        prog(30, "Chat already fetched")
    elif "chat" in basis or chat_file:
        msgs, meta = chat.fetch(platform, url, vid, jd, prog, cookies_browser=cookies_browser, chat_file=chat_file)
        write_json(jd / "chat.json", msgs)
        write_json(jd / "meta.json", meta)
    else:
        prog(2, "Reading video info")
        meta = chat.MODS[platform].meta_of(url, cookies_browser)
        write_json(jd / "meta.json", meta)
    if not meta.get("duration"):
        raise RuntimeError("Couldn't tell how long this stream is.")

    db, words = None, None
    if "audio" in basis or "speech" in basis:
        speech = "speech" in basis
        if speech and not stt.available():
            raise RuntimeError("Finding clips by what's said needs the speech-to-text model. Run `clipper stt-download`.")
        old = read_json(jd / "signals.json", {}) or {}
        if old.get("db") and (not speech or (jd / "transcript.json").exists()):
            db, words = old["db"], read_json(jd / "transcript.json") if speech else None
        else:
            scan = signals.scan_url(url, meta["duration"], speech=speech, cookies_browser=cookies_browser,
                                    progress=lambda pct, msg: set_status(jd, "listen", pct, msg))
            db, words = scan.db, (scan.words if speech else None)
            write_json(jd / "signals.json", {"bin": signals.BIN, "db": db})
            if speech:
                write_json(jd / "transcript.json", words)

    names = ", ".join(signals.NAMES[b].lower() for b in basis)
    set_status(jd, "spikes", 32, f"Finding moments by {names}")
    if "chat" in basis and not msgs:
        raise RuntimeError("No chat to find spikes in. Untick chat spikes or add a chat file.")
    res = signals.find(basis, meta["duration"], msgs=msgs, db=db, words=words, keywords=keywords, top_n=top_n)
    old = {c["id"]: c for c in (read_json(jd / "clips.json", {}) or {}).get("clips", [])}
    for c in res["clips"]:
        c["vod_time"] = fmt_t(c["start"])
        prev = old.get(c["id"])           # keep Claude's notes if the same moment is re-analysed
        if prev and abs(prev["chat_peak"] - c["chat_peak"]) < 1:
            c["what"], c["why"] = prev.get("what", ""), prev.get("why", "")
        else:
            c["what"], c["why"] = "", ""
    summary = {"url": url, "platform": platform, "id": vid, "title": meta.get("title"), "channel": meta.get("channel"),
               "duration": meta["duration"], "messages": len(msgs), "avg_per_min": res.get("avg_per_min", 0),
               "bin": res["bin"], "basis": basis, "keywords": keywords, "clips": res["clips"]}
    if msgs:
        write_json(jd / "rates.json", {"bin": res["bin"], "avg": res["avg"], "counts": res["counts"]})
    else:
        (jd / "rates.json").unlink(missing_ok=True)
    write_json(jd / "clips.json", summary)
    if not res["clips"]:
        set_status(jd, "done", 100, f"No moments found by {names} in this stream.")
        return
    if skip_download:
        set_status(jd, "done", 100, "Spikes found (download skipped)")
        return

    n = len(res["clips"])
    for i, c in enumerate(res["clips"]):
        base = 35 + 65 * i / n
        step = 65 / n
        cd = jd / "clips" / str(c["id"])
        cd.mkdir(parents=True, exist_ok=True)
        if (cd / "source.mp4").exists() and (read_json(cd / "clip.json") or {}).get("start") == c["start"]:
            continue
        for stale in ("edit.json", "final.mp4", "final.srt", "props.json"):
            (cd / stale).unlink(missing_ok=True)
        set_status(jd, "download", base, f"Clip {c['id']}/{n}: downloading {fmt_t(c['start'])}-{fmt_t(c['end'])} (best quality)")
        src, info = download.section(url, c["start"], c["end"], cd, cookies_browser)
        prepare_clip(cd, c, src, info, lambda f, m: set_status(jd, "prepare", base + step * f, f"Clip {c['id']}/{n}: {m}"))
    set_status(jd, "done", 100, f"{n} clips ready")


def prepare_clip(cd: Path, c, src, info, progress=lambda frac, msg: None):
    """After the clip's source.mp4 exists: hot moment, word timings, face crop, clip.json, edit.json."""
    progress(0.5, "finding the hot moment")
    hook = hotspot.find(src, c["start"], c.get("hot_ref", c["chat_peak"]), info["duration"])
    progress(0.6, "transcribing for subtitles")
    try:
        subs.words(src, cd / "words.json")
    except Exception as e:  # subtitles are optional; the clip still works
        write_json(cd / "words.json", [])
        (cd / "subs_error.log").write_text(str(e))
    progress(0.85, "finding the face cam")
    rects = face.suggest(src, info)
    clip_meta = {"id": c["id"], "start": c["start"], "end": c["end"], "info": info, "hook": list(hook),
                 "face_found": rects["found"]}
    write_json(cd / "clip.json", clip_meta)
    if not (cd / "edit.json").exists():
        write_json(cd / "edit.json", render.default_edit(clip_meta, info, rects))
    return clip_meta
