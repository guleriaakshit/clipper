"""Twitch VOD chat via Twitch's internal web API (gql.twitch.tv), authorised by the user 2026-11-17.

No login. Cursor paging fails Twitch's integrity check, so we page by content offset:
ask for comments at second S, Twitch returns a page (~60) starting at or before S; continue from the
last comment's second, de-duplicating by comment id. A chat file (--chat-file) still works as a fallback.
"""
import json
import subprocess
import time
import urllib.request

GQL = "https://gql.twitch.tv/gql"
CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"     # Twitch's public web client id (same one twitch.tv and yt-dlp use)
Q = ('query{video(id:"%s"){comments(contentOffsetSeconds:%d){edges{node{id contentOffsetSeconds '
     'commenter{displayName} message{fragments{text}}}} pageInfo{hasNextPage}}}}')


def meta_of(url, cookies_browser=None):
    cmd = ["yt-dlp", "-J", "--no-warnings", "--skip-download", url]
    if cookies_browser:
        cmd[1:1] = ["--cookies-from-browser", cookies_browser]
    j = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
    return {"title": j.get("title"), "channel": j.get("uploader") or j.get("channel"), "duration": j.get("duration")}


def _page(vid, offset, tries=5):
    body = json.dumps({"query": Q % (vid, offset)}).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(GQL, data=body, headers={"Client-Id": CLIENT_ID, "Content-Type": "application/json",
                                                                  "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.loads(r.read())
            if j.get("errors"):
                raise RuntimeError(j["errors"][0].get("message", "GQL error"))
            v = (j.get("data") or {}).get("video")
            if v is None:
                raise RuntimeError("Twitch says this VOD doesn't exist or isn't public.")
            c = v["comments"] or {"edges": [], "pageInfo": {"hasNextPage": False}}
            return [e["node"] for e in c["edges"]], c["pageInfo"]["hasNextPage"]
        except RuntimeError:
            raise
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def _range(vid, start, end, seen, msgs, lock, done):
    """Page one stretch of the VOD [start, end)."""
    offset = start
    while offset < end:
        nodes, more = _page(vid, offset)
        new = 0
        with lock:
            for n in nodes:
                if n["id"] in seen or n["contentOffsetSeconds"] >= end + 1:
                    continue
                seen.add(n["id"])
                new += 1
                text = "".join((f.get("text") or "") for f in (n.get("message") or {}).get("fragments") or [])
                msgs.append({"t": float(n["contentOffsetSeconds"]),
                             "user": (n.get("commenter") or {}).get("displayName", ""), "text": text})
        last = max((n["contentOffsetSeconds"] for n in nodes), default=offset)
        if not more or not nodes:
            break
        # stay on the last second once (a page can end mid-second); move on if nothing new came back
        offset = max(offset, last) + 1 if (new == 0 or last <= offset) else last
        done[start] = min(end, offset) - start
        time.sleep(0.1)
    done[start] = end - start


def fetch(url, vid, workdir, progress, cookies_browser=None, workers=8):
    import threading
    progress(2, "Reading video info")
    meta = meta_of(url, cookies_browser)
    dur = float(meta.get("duration") or 1)
    seen, msgs, lock, done = set(), [], threading.Lock(), {}
    step = dur / workers
    starts = [int(i * step) for i in range(workers)]
    ends = starts[1:] + [int(dur) + 60]
    errs = []

    def run(a, b):
        try:
            _range(vid, a, b, seen, msgs, lock, done)
        except Exception as e:
            errs.append(e)
    ts = [threading.Thread(target=run, args=(a, b), daemon=True) for a, b in zip(starts, ends)]
    for t in ts:
        t.start()
    while any(t.is_alive() for t in ts):
        got = sum(done.values())
        progress(2 + 28 * min(1, got / dur), f"Twitch chat: {len(msgs):,} messages, {int(got // 60)}/{int(dur // 60)} min fetched")
        time.sleep(1)
    if errs:
        raise RuntimeError(f"Twitch chat fetch failed: {errs[0]}")
    return msgs, meta
