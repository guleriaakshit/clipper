"""analyze <url>: chat (and, if picked, the streamer's voice and words) -> moments -> download only those parts
-> hook, words, face crop. Progress for each step goes to status.json for the dashboard's bars."""
import time
import traceback
from pathlib import Path

from . import captions, chat, download, face, hotspot, listen, render, signals, stt, subs
from .common import job_dir, read_json, write_json, fmt_t
from .platforms import detect

PLATFORM_NAME = {"twitch": "Twitch", "youtube": "YouTube", "kick": "Kick"}


def analyze(url, top_n=10, cookies_browser=None, chat_file=None, skip_download=False, basis=("chat",), keywords="",
            min_score=85, chat_x=2.0, loud_x=2.0):
    """basis: any of "chat", "audio", "speech" (see signals.py). chat_x / loud_x: chat must reach this many times its
    average rate, the streamer's voice this many times their usual level, to count as a spike."""
    platform, vid = detect(url)
    jd = job_dir(platform, vid)
    basis = [b for b in ("chat", "audio", "speech") if b in (basis or ())] or ["chat"]
    write_json(jd / "job.json", {"url": url, "platform": platform, "id": vid, "cookies_browser": cookies_browser,
                                 "basis": basis, "keywords": keywords, "min_score": min_score, "chat_x": chat_x,
                                 "loud_x": loud_x})
    (jd / "stop").unlink(missing_ok=True)
    tr = Tracker(jd)
    # steps that can't happen for this run are hidden from the start
    if "audio" not in basis and "speech" not in basis:
        tr.skip("audio")
        tr.skip("listen")
    try:
        _run(url, platform, vid, jd, tr, top_n, cookies_browser, chat_file, skip_download, basis, keywords, min_score,
             chat_x, loud_x)
    except Exception as e:
        tr.fail(f"{e}")
        (jd / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        stt.hq_release()                   # free the GPU however the analysis ends
    return jd


class Stopped(RuntimeError):
    pass


# step key, label on the dashboard, share of the overall bar (shares of skipped steps are left out)
STEPS = [("chat", "Chat replay", 15), ("subs", "Subtitles from the platform", 2),
         ("audio", "Download the stream's audio", 18), ("listen", "Listen and transcribe", 40),
         ("moments", "Find the moments", 2), ("clips", "Download and prepare clips", 23)]


class Tracker:
    """Progress per step in status.json: {"stage", "pct", "msg", "error", "steps": [{key, label, state, pct, msg}]},
    state = wait | run | done | skip | failed. Every update is also where a Stop from the dashboard takes effect."""

    def __init__(self, jd: Path):
        self.jd = jd
        self.steps = [{"key": k, "label": l, "state": "wait", "pct": 0.0, "msg": "", "w": w} for k, l, w in STEPS]
        self.cur = None
        self._last = 0.0

    def _get(self, key):
        return next(s for s in self.steps if s["key"] == key)

    def label(self, key, text):
        self._get(key)["label"] = text

    def step(self, key, pct, msg=""):
        if (self.jd / "stop").exists():
            raise Stopped("Stopped by you.")
        if self.cur and self.cur != key and self._get(self.cur)["state"] == "run":
            self._get(self.cur).update(state="done", pct=100.0)       # moving on: the step before is done
        self.cur = key
        self._get(key).update(state="run", pct=max(0.0, min(100.0, float(pct))), msg=msg)
        self._write()

    def done(self, key, msg=None):
        self._get(key).update(state="done", pct=100.0, **({"msg": msg} if msg is not None else {}))
        self._write(force=True)

    def skip(self, key, why=""):
        self._get(key).update(state="skip", pct=0.0, msg=why)
        self._write(force=True)

    def overall(self):
        live = [s for s in self.steps if s["state"] != "skip"]
        tot = sum(s["w"] for s in live) or 1
        return 100 * sum(s["w"] * (100 if s["state"] == "done" else s["pct"]) / 100 for s in live) / tot

    def _status(self, stage, msg, error=""):
        return {"stage": stage, "pct": round(self.overall(), 1), "msg": msg, "error": error,
                "steps": [{k: v for k, v in s.items() if k != "w"} for s in self.steps]}

    def _write(self, force=False):
        now = time.time()
        if not force and now - self._last < 0.5:      # the dashboard looks every 2 s; don't rewrite the file 100x/s
            return
        self._last = now
        s = self._get(self.cur) if self.cur else None
        write_json(self.jd / "status.json", self._status(s["label"] if s else "starting", s["msg"] if s else ""))

    def finish(self, msg):
        for s in self.steps:
            if s["state"] == "run":
                s.update(state="done", pct=100.0)
            elif s["state"] == "wait":
                s["state"] = "skip"
        write_json(self.jd / "status.json", {**self._status("done", msg), "pct": 100})

    def fail(self, error):
        if self.cur and self._get(self.cur)["state"] == "run":
            self._get(self.cur)["state"] = "failed"
        write_json(self.jd / "status.json", self._status("error", "", error))


def _run(url, platform, vid, jd: Path, tr: Tracker, top_n, cookies_browser, chat_file, skip_download, basis, keywords,
         min_score, chat_x=2.0, loud_x=2.0):
    pname = PLATFORM_NAME.get(platform, platform)
    # the chat readers report 0-30 (their old share of a single bar): stretch that to the chat step's own 0-100
    prog = lambda pct, msg: tr.step("chat", min(99.0, pct * 100 / 30), msg)
    msgs = []
    if (jd / "chat.json").exists() and not chat_file:
        msgs, meta = read_json(jd / "chat.json"), read_json(jd / "meta.json")
        tr.done("chat", f"Already fetched: {len(msgs):,} messages")
    elif "chat" in basis or chat_file:
        msgs, meta = chat.fetch(platform, url, vid, jd, prog, cookies_browser=cookies_browser, chat_file=chat_file)
        write_json(jd / "chat.json", msgs)
        write_json(jd / "meta.json", meta)
        tr.done("chat", f"{len(msgs):,} messages")
    else:
        tr.label("chat", "Video info")
        tr.step("chat", 50, "Reading video info")
        meta = chat.MODS[platform].meta_of(url, cookies_browser)
        write_json(jd / "meta.json", meta)
        tr.done("chat", "")
    if not meta.get("duration"):
        raise RuntimeError("Couldn't tell how long this stream is.")

    # The platform's own subtitles first, always: they give "what's said" and the clips' subtitles without any
    # speech-to-text. Only when there are none is anything transcribed: the whole stream when "what's said" is
    # picked (listen.py), otherwise just the clips. Streamer gets loud: always from the downloaded audio.
    notes, words, db = [], None, None
    tr.step("subs", 10, f"Looking for {pname}'s own subtitles")
    try:
        words = captions.fetch(url, jd, platform, cookies_browser)
    except Exception:                                    # no subtitles is the normal case on Twitch and Kick
        words = None
    if words:
        tr.done("subs", f"{len(words):,} words from {pname}'s subtitles, so nothing needs transcribing")
    else:
        tr.skip("subs", f"{pname} has no subtitles for this stream, so Clipper transcribes it")
    need_words = "speech" in basis and not words
    if need_words and not stt.can_transcribe(past=True):
        notes.append(f"{pname} has no subtitles for this stream and the speech-to-text model isn't installed "
                     f"(run: clipper stt-download), so what's said was skipped.")
        basis = [b for b in basis if b != "speech"]
        need_words = False
    if "audio" in basis or need_words:
        tr.label("listen", f"Transcribe ({listen.engine()})" if need_words else "Measure the streamer's voice")
        db, heard = listen.listen(url, jd, meta["duration"], need_words, cookies_browser, tr.step)
        tr.done("audio")
        tr.done("listen", f"{len(heard):,} words" if need_words and heard is not None else "Voice levels read")
        if need_words:
            words = heard
    else:
        tr.skip("audio")
        tr.skip("listen")
    if "speech" in basis and not words:
        basis = [b for b in basis if b != "speech"]
        notes.append("No words could be read in this stream, so what's said was skipped.")
    if not basis:
        raise RuntimeError(" ".join(notes) + " Pick chat spikes for this stream.")
    if notes:
        j = read_json(jd / "job.json", {}) or {}
        write_json(jd / "job.json", {**j, "basis_used": basis, "notes": notes})

    names = ", ".join(signals.NAMES[b].lower() for b in basis)
    tr.step("moments", 30, f"Finding moments by {names}")
    if "chat" in basis and not msgs:
        raise RuntimeError("No chat to find spikes in. Untick chat spikes or add a chat file.")
    res = signals.find(basis, meta["duration"], msgs=msgs, db=db, words=words, keywords=keywords, top_n=top_n,
                       min_score=min_score, chat_x=chat_x, loud_x=loud_x)
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
               "bin": res["bin"], "basis": basis, "keywords": keywords, "notes": notes, "min_score": min_score,
               "below": res.get("below"), "chat_x": chat_x, "loud_x": loud_x, "usual_db": res.get("usual_db"),
               "clips": res["clips"]}
    if msgs:
        write_json(jd / "rates.json", {"bin": res["bin"], "avg": res["avg"], "counts": res["counts"]})
    else:
        (jd / "rates.json").unlink(missing_ok=True)
    write_json(jd / "clips.json", summary)
    n = len(res["clips"])
    tr.done("moments", f"{n} moment{'' if n == 1 else 's'} scored {min_score} or more")
    if not res["clips"]:
        b = res.get("below")
        tr.finish((f"No clip scored {min_score} or more. The best was {b['score']} at {fmt_t(b['start'])}. "
                   f"Lower the minimum score to get it.") if b else f"No moments found by {names} in this stream.")
        return
    if skip_download:
        tr.finish("Moments found (download skipped)")
        return

    for i, c in enumerate(res["clips"]):
        cd = jd / "clips" / str(c["id"])
        cd.mkdir(parents=True, exist_ok=True)
        if (cd / "source.mp4").exists() and (read_json(cd / "clip.json") or {}).get("start") == c["start"]:
            continue
        for stale in ("edit.json", "final.mp4", "final.srt", "props.json", "fx.json", "brief.md"):
            (cd / stale).unlink(missing_ok=True)
        tr.step("clips", 100 * i / n,
                f"Clip {c['id']} of {n}: downloading {fmt_t(c['start'])}-{fmt_t(c['end'])} (best quality)")
        src, info = download.section(url, c["start"], c["end"], cd, cookies_browser)
        prepare_clip(cd, c, src, info,
                     lambda f, m: tr.step("clips", 100 * (i + 0.5 + 0.5 * f) / n, f"Clip {c['id']} of {n}: {m}"),
                     captions_words=words, past=True)
    tr.finish(f"{n} clips ready")


def prepare_clip(cd: Path, c, src, info, progress=lambda frac, msg: None, captions_words=None, past=False):
    """After the clip's source.mp4 exists: hot moment, word timings, face crop, clip.json, edit.json."""
    progress(0.5, "finding the hot moment")
    hook = hotspot.find(src, c["start"], c.get("hot_ref", c["chat_peak"]), info["duration"])
    try:
        if captions_words:                 # the stream's subtitles or its transcript, cut to this clip
            progress(0.6, "subtitles from the stream's words")
            write_json(cd / "words.json", captions.clip_words(captions_words, c["start"], c["start"] + info["duration"]))
        else:                              # no subtitles for the stream (Twitch/Kick, live): transcribe this clip
            model = stt.HQ_NAME if past and stt.hq_available() else stt.model_name()
            progress(0.6, f"transcribing this clip for subtitles ({model})")
            subs.words(src, cd / "words.json", past=past)
    except Exception as e:  # subtitles are optional; the clip still works
        write_json(cd / "words.json", [])
        (cd / "subs_error.log").write_text(str(e), encoding="utf-8")
    progress(0.85, "finding the face cam")
    rects = face.suggest(src, info)
    clip_meta = {"id": c["id"], "start": c["start"], "end": c["end"], "info": info, "hook": list(hook),
                 "face_found": rects["found"]}
    write_json(cd / "clip.json", clip_meta)
    if not (cd / "edit.json").exists():
        write_json(cd / "edit.json", render.default_edit(clip_meta, info, rects))
    return clip_meta
