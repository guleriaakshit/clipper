"""YouTube chat replay via yt-dlp's live_chat subtitle track (JSON lines)."""
import json
import subprocess
from pathlib import Path


def _text(runs):
    out = []
    for r in runs or []:
        if "text" in r:
            out.append(r["text"])
        elif "emoji" in r:
            e = r["emoji"]
            sc = (e.get("shortcuts") or [None])[0]
            out.append(sc or e.get("emojiId", ""))
    return "".join(out)


def meta_of(url, cookies_browser=None):
    cmd = ["yt-dlp", "-J", "--no-warnings", "--skip-download", url]
    if cookies_browser:
        cmd[1:1] = ["--cookies-from-browser", cookies_browser]
    j = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
    return {"title": j.get("title"), "channel": j.get("channel") or j.get("uploader"),
            "duration": j.get("duration"), "has_chat": "live_chat" in (j.get("subtitles") or {})}


def fetch(url, vid, workdir: Path, progress, cookies_browser=None):
    progress(2, "Reading video info")
    meta = meta_of(url, cookies_browser)
    if not meta["has_chat"]:
        raise RuntimeError("This YouTube video has no chat replay (the stream had it turned off, or it wasn't a live stream).")
    progress(5, "Downloading YouTube chat replay (can take a few minutes for long streams)")
    out = workdir / "yt_chat"
    cmd = ["yt-dlp", "--skip-download", "--write-subs", "--sub-langs", "live_chat", "--no-warnings",
           "-o", str(out), url]
    if cookies_browser:
        cmd[1:1] = ["--cookies-from-browser", cookies_browser]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    path = workdir / "yt_chat.live_chat.json"
    msgs = []
    dur = meta["duration"] or 1
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            r = d.get("replayChatItemAction") or {}
            off = r.get("videoOffsetTimeMsec")
            if off is None:
                continue
            t = int(off) / 1000.0
            for a in r.get("actions", []):
                item = (a.get("addChatItemAction") or {}).get("item") or {}
                m = (item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer"))
                if not m:
                    continue
                msgs.append({"t": max(0.0, t), "user": (m.get("authorName") or {}).get("simpleText", ""),
                             "text": _text((m.get("message") or {}).get("runs"))})
            if i % 2000 == 0:
                progress(5 + 25 * min(1, t / dur), f"Parsed chat up to {int(t // 60)} min")
    path.unlink(missing_ok=True)
    return msgs, meta
