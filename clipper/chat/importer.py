"""Read a chat file exported by another tool, for platforms Clipper can't fetch on its own yet.

Accepted formats (auto-detected):
- TwitchDownloader JSON: {"comments": [{"content_offset_seconds", "commenter": {"display_name"}, "message": {"body"}}]}
- chat-downloader JSON: [{"time_in_seconds", "author": {"name"}, "message"}]
- Clipper JSON: [{"t", "user", "text"}]
- CSV with header t,user,text (t in seconds, or h:mm:ss)
"""
import csv
import json
from pathlib import Path


def _secs(v):
    if isinstance(v, (int, float)):
        return float(v)
    parts = [float(p) for p in str(v).strip().split(":")]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


def load(path):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8") as f:
            return [{"t": _secs(r["t"]), "user": r.get("user", ""), "text": r.get("text", "")} for r in csv.DictReader(f)]
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "comments" in data:
        return [{"t": float(c["content_offset_seconds"]),
                 "user": (c.get("commenter") or {}).get("display_name", ""),
                 "text": (c.get("message") or {}).get("body", "")} for c in data["comments"]]
    if isinstance(data, list) and data and "time_in_seconds" in data[0]:
        return [{"t": float(c["time_in_seconds"]), "user": (c.get("author") or {}).get("name", ""),
                 "text": c.get("message") or ""} for c in data if c.get("time_in_seconds", -1) >= 0]
    if isinstance(data, list) and (not data or "t" in data[0]):
        return [{"t": float(c["t"]), "user": c.get("user", ""), "text": c.get("text", "")} for c in data]
    raise ValueError(f"Unknown chat file format: {path.name}")
