"""Pre-existing memes for a clip, most with sound:
- video memes with sound: short meme clips (40 s or less) found through yt-dlp's YouTube search, saved as MP4;
- meme sounds: sound buttons from myinstants.com, saved as MP3;
- classic meme images: memegen.link + Imgflip's top 100 (free, no key); top/bottom text drawn locally in Impact.
Saved files land in clips/<n>/memes/ with a memes.json log of where each came from."""
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from .common import TMP, font as font_of, read_json, write_json

MEDIA_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".webm", ".mov", ".mp3", ".m4a", ".wav", ".ogg")
AUDIO_EXT = (".mp3", ".m4a", ".wav", ".ogg")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
INDEX = TMP / "meme_index.json"
INDEX_TTL = 24 * 3600


class MemeError(RuntimeError):
    pass


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    return dest


# ---------------- classic meme images (memegen.link + Imgflip) ----------------
def _index():
    """All classic templates, cached in Clipper/tmp for a day."""
    c = read_json(INDEX)
    if c and time.time() - c.get("at", 0) < INDEX_TTL:
        return c["items"]
    items, seen = [], set()
    try:
        for t in _get_json("https://api.memegen.link/templates/"):
            items.append({"id": f"memegen:{t['id']}", "name": t["name"], "source": "memegen.link", "kind": "image",
                          "url": t["blank"], "preview": t["blank"], "memegen": t["id"], "lines": t.get("lines", 2),
                          "words": " ".join([t["id"], t["name"], *t.get("keywords", [])]).lower()})
            seen.add(t["name"].lower())
    except Exception as ex:
        if not items:
            print("memegen.link unavailable:", ex)
    try:
        for t in _get_json("https://api.imgflip.com/get_memes")["data"]["memes"]:
            if t["name"].lower() in seen:
                continue
            items.append({"id": f"imgflip:{t['id']}", "name": t["name"], "source": "Imgflip", "kind": "image",
                          "url": t["url"], "preview": t["url"], "memegen": None, "lines": t.get("box_count", 2),
                          "words": t["name"].lower()})
    except Exception as ex:
        print("Imgflip unavailable:", ex)
    if not items:
        raise MemeError("Couldn't reach memegen.link or Imgflip. Check the internet connection.")
    write_json(INDEX, {"at": time.time(), "items": items})
    return items


def classic(query="", limit=60):
    items = _index()
    q = [w for w in query.lower().split() if w]
    if not q:
        return sorted(items, key=lambda it: it["source"] != "Imgflip")[:limit]   # Imgflip list is ranked by popularity
    scored = [(sum(w in it["words"] for w in q), it) for it in items]
    return [it for s, it in sorted(scored, key=lambda x: -x[0]) if s][:limit]


# ---------------- video memes with sound (YouTube search through yt-dlp) ----------------
MAX_VIDEO_S = 40


def videos(query="", limit=24):
    q = (query.strip() or "reaction") + " meme"
    try:
        p = subprocess.run(["yt-dlp", "--no-warnings", "--flat-playlist", "-J", f"ytsearch{limit * 3}:{q}"],
                           capture_output=True, text=True, timeout=90)
        rows = json.loads(p.stdout).get("entries") or []
    except FileNotFoundError:
        raise MemeError("yt-dlp isn't installed. Install it (brew install yt-dlp) and try again.")
    except (subprocess.TimeoutExpired, ValueError):
        raise MemeError("YouTube search didn't answer. Try again in a moment.")
    out = []
    for e in rows:
        d = e.get("duration")
        if not e.get("id") or not d or d > MAX_VIDEO_S:
            continue
        out.append({"id": f"yt:{e['id']}", "name": e.get("title") or "Meme", "source": "YouTube", "kind": "video",
                    "yt": e["id"], "url": f"https://www.youtube.com/watch?v={e['id']}", "duration": d,
                    "preview": f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg", "memegen": None, "lines": 0,
                    "page": f"https://www.youtube.com/watch?v={e['id']}"})
        if len(out) >= limit:
            break
    return out


# ---------------- meme sounds (myinstants.com) ----------------
_SOUND_RX = re.compile(r"play\('(/media/sounds/[^']+)'.*?class=\"instant-link[^\"]*\">([^<]+)</a>", re.S)


def sounds(query="", limit=40):
    from html import unescape
    from urllib.parse import quote
    url = (f"https://www.myinstants.com/en/search/?name={quote(query.strip())}" if query.strip()
           else "https://www.myinstants.com/en/index/us/")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            page = r.read().decode("utf-8", "replace")
    except Exception as ex:
        raise MemeError(f"Couldn't reach myinstants.com ({ex}).")
    out, seen = [], set()
    for path, name in _SOUND_RX.findall(page):
        if path in seen:
            continue
        seen.add(path)
        name = unescape(name).strip()
        out.append({"id": f"myinstants:{path.rsplit('/', 1)[-1]}", "name": name, "source": "Myinstants", "kind": "sound",
                    "url": "https://www.myinstants.com" + path, "preview": None, "memegen": None, "lines": 0,
                    "page": None})
        if len(out) >= limit:
            break
    return out


SOURCES = {"videos": videos, "sounds": sounds, "classic": classic}


def search(query, source):
    return SOURCES.get(source, videos)(query)


# ---------------- captions + saving ----------------
CACHE = TMP / "meme_cache"


def blank(item) -> Path:
    """The meme's original file, cached in Clipper/tmp."""
    CACHE.mkdir(exist_ok=True)
    if item.get("yt"):
        dest = CACHE / f"yt_{item['yt']}.mp4"
        if not dest.exists() or not dest.stat().st_size:
            p = subprocess.run(["yt-dlp", "--no-warnings", "--no-playlist", "-f", "bv*[height<=1080]+ba/b",
                                "-S", "res,fps", "--merge-output-format", "mp4", "--paths", f"temp:{TMP}",
                                "-o", str(dest), item["url"]], capture_output=True, text=True, timeout=300)
            if p.returncode != 0 or not dest.exists():
                raise MemeError("Couldn't download that meme: " + (p.stderr.strip().splitlines() or ["unknown error"])[-1])
        return dest
    ext = os.path.splitext(item["url"].split("?")[0])[1].lower()
    dest = CACHE / (re.sub(r"[^A-Za-z0-9]+", "_", item["id"]) + (ext if ext in MEDIA_EXT else ".png"))
    if not dest.exists() or not dest.stat().st_size:
        _download(item["url"], dest)
    return dest


def _fit(draw, text, W, H):
    """Biggest Impact size where the text wraps into at most 3 lines inside the image."""
    from PIL import ImageFont
    words = text.split()
    for size in range(int(H * 0.13), 11, -2):
        font = font_of("impact", size)
        lines, cur = [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if draw.textlength(t, font=font) <= W * 0.94 or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        if len(lines) <= 3 and all(draw.textlength(l, font=font) <= W * 0.94 for l in lines):
            return font, lines, size
    return font_of("impact", 12), [text], 12


def captioned(item, top="", bottom=""):
    """Classic white Impact text with a black outline, drawn locally (no watermark). Images only."""
    from PIL import Image, ImageDraw
    img = Image.open(blank(item)).convert("RGB")
    d = ImageDraw.Draw(img)
    W, H = img.size
    for text, at_top in ((top, True), (bottom, False)):
        text = (text or "").strip().upper()
        if not text:
            continue
        font, lines, size = _fit(d, text, W, H)
        lh = int(size * 1.08)
        y = int(H * 0.03) if at_top else H - int(H * 0.03) - lh * len(lines)
        for i, l in enumerate(lines):
            d.text((W / 2, y + i * lh), l, font=font, fill="white", anchor="ma",
                   stroke_width=max(2, size // 14), stroke_fill="black")
    return img


def save(clip_dir: Path, item, top="", bottom=""):
    md = clip_dir / "memes"
    md.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", item["name"].lower()).strip("-")[:40] or "meme"
    stem = md / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}"
    if item["kind"] == "image" and (top.strip() or bottom.strip()):
        dest = stem.with_suffix(".png")
        captioned(item, top, bottom).save(dest)
    else:
        src = blank(item)
        dest = stem.with_suffix(src.suffix)
        shutil.copyfile(src, dest)
    log = read_json(md / "memes.json", [])
    log.append({"file": dest.name, "name": item["name"], "source": item["source"], "url": item["url"],
                "page": item.get("page"), "top": top, "bottom": bottom})
    write_json(md / "memes.json", log)
    return str(dest)


def saved(clip_dir: Path):
    md = clip_dir / "memes"
    if not md.is_dir():
        return []
    return sorted((str(p) for p in md.iterdir() if p.suffix.lower() in MEDIA_EXT), reverse=True)


def saved_visual(clip_dir: Path):
    """Saved memes a gallery can show (images and videos, not sounds)."""
    return [f for f in saved(clip_dir) if Path(f).suffix.lower() not in AUDIO_EXT]
