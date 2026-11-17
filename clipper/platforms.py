"""URL -> (platform, video id)."""
import re

PATTERNS = [
    ("twitch", re.compile(r"twitch\.tv/(?:videos/|[^/]+/v(?:ideo)?/)(\d+)")),
    ("youtube", re.compile(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|live/|shorts/|embed/)|youtu\.be/)([\w-]{11})")),
    ("kick", re.compile(r"kick\.com/(?:[^/]+/videos|video)/([0-9a-f-]{36})", re.I)),
]


def detect(url: str):
    url = url.strip()
    for name, rx in PATTERNS:
        m = rx.search(url)
        if m:
            return name, m.group(1)
    raise ValueError(
        "Couldn't read that link. Use a past-stream link like "
        "https://www.twitch.tv/videos/123…, https://www.youtube.com/watch?v=… or https://kick.com/<channel>/videos/<uuid>")
