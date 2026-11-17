"""analyze <url>: chat -> spikes -> download only the spike parts -> hook, words, face crop."""
import traceback
from pathlib import Path

from . import chat, spikes, download, hotspot, subs, face, render
from .common import job_dir, read_json, set_status, write_json, fmt_t
from .platforms import detect


def analyze(url, top_n=10, cookies_browser=None, chat_file=None, skip_download=False):
    platform, vid = detect(url)
    jd = job_dir(platform, vid)
    write_json(jd / "job.json", {"url": url, "platform": platform, "id": vid, "cookies_browser": cookies_browser})
    try:
        _run(url, platform, vid, jd, top_n, cookies_browser, chat_file, skip_download)
    except Exception as e:
        set_status(jd, "error", 0, "", f"{e}")
        (jd / "error.log").write_text(traceback.format_exc())
        raise
    return jd


def _run(url, platform, vid, jd: Path, top_n, cookies_browser, chat_file, skip_download):
    prog = lambda pct, msg: set_status(jd, "chat", pct, msg)
    if (jd / "chat.json").exists() and not chat_file:
        msgs, meta = read_json(jd / "chat.json"), read_json(jd / "meta.json")
        prog(30, "Chat already fetched")
    else:
        msgs, meta = chat.fetch(platform, url, vid, jd, prog, cookies_browser=cookies_browser, chat_file=chat_file)
        write_json(jd / "chat.json", msgs)
        write_json(jd / "meta.json", meta)

    set_status(jd, "spikes", 32, f"{len(msgs)} chat messages. Finding spikes")
    res = spikes.analyse(msgs, meta["duration"], top_n=top_n)
    old = {c["id"]: c for c in (read_json(jd / "clips.json", {}) or {}).get("clips", [])}
    for c in res["clips"]:
        c["vod_time"] = fmt_t(c["start"])
        prev = old.get(c["id"])           # keep Claude's notes if the same moment is re-analysed
        if prev and abs(prev["chat_peak"] - c["chat_peak"]) < 1:
            c["what"], c["why"] = prev.get("what", ""), prev.get("why", "")
        else:
            c["what"], c["why"] = "", ""
    summary = {"url": url, "platform": platform, "id": vid, "title": meta.get("title"), "channel": meta.get("channel"),
               "duration": meta["duration"], "messages": len(msgs), "avg_per_min": res["avg_per_min"],
               "bin": res["bin"], "clips": res["clips"]}
    write_json(jd / "rates.json", {"bin": res["bin"], "avg": res["avg"], "counts": res["counts"]})
    write_json(jd / "clips.json", summary)
    if not res["clips"]:
        set_status(jd, "done", 100, "No chat spikes of 2x the average or more in this stream.")
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
    hook = hotspot.find(src, c["start"], c["chat_peak"], info["duration"])
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
