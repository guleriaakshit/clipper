"""Chat fetchers. Each returns (messages, meta).
messages: [{"t": seconds_into_vod, "user": str, "text": str}] sorted by t.
meta: {"title", "channel", "duration"}"""
from . import youtube, twitch, kick, importer

MODS = {"youtube": youtube, "twitch": twitch, "kick": kick}


def fetch(platform, url, vid, workdir, progress=lambda pct, msg: None, cookies_browser=None, chat_file=None):
    mod = MODS[platform]
    if chat_file:
        progress(2, "Reading video info")
        meta = mod.meta_of(url, cookies_browser)
        progress(10, "Reading chat file")
        msgs = importer.load(chat_file)
    else:
        msgs, meta = mod.fetch(url, vid, workdir, progress, cookies_browser=cookies_browser)
    msgs = [m for m in msgs if m["t"] >= 0]
    msgs.sort(key=lambda m: m["t"])
    if not msgs:
        raise RuntimeError("No chat messages found for this video.")
    if not meta.get("duration"):
        meta["duration"] = msgs[-1]["t"] + 5
    return msgs, meta
