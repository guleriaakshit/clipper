"""Kick VOD chat.

Automatic fetching is NOT built yet: Kick's chat history sits behind a Cloudflare bot check,
and the user's rule is never to bypass a site's bot check. Until an allowed route exists:
export the chat to a file (Clipper JSON/CSV: t,user,text) and pass it with --chat-file,
or drop it on the dashboard.
"""
import json
import subprocess


def meta_of(url, cookies_browser=None):
    cmd = ["yt-dlp", "-J", "--no-warnings", "--skip-download", url]
    if cookies_browser:
        cmd[1:1] = ["--cookies-from-browser", cookies_browser]
    j = json.loads(subprocess.run(cmd, check=True, capture_output=True, text=True).stdout)
    return {"title": j.get("title"), "channel": j.get("uploader") or j.get("channel"), "duration": j.get("duration")}


def fetch(url, vid, workdir, progress, cookies_browser=None):
    raise RuntimeError(
        "Kick chat can't be fetched automatically (Cloudflare bot check). Add an exported chat file "
        "with the 'Chat file' box on the dashboard or `clipper analyze <url> --chat-file chat.json`.")
