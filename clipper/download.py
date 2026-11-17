"""Download only the clip's time range, best resolution and highest frame rate."""
import re
import subprocess
from pathlib import Path

from .common import TMP, video_info

# res first, then fps, then bitrate: the best resolution available, and among those the highest frame rate
SORT = "res,fps,vbr,abr"


def _source(url):
    """yt-dlp's Kick VOD reader can lag behind Kick; Kick's own video data gives the HLS playlist directly."""
    m = re.search(r"kick\.com/.*?([0-9a-f]{8}-[0-9a-f-]{27})", url)
    if m:
        from .chat import kick
        try:
            return kick.video(m.group(1)).get("source") or url
        except Exception:
            return url
    return url


def section(url, start, end, out_dir: Path, cookies_browser=None):
    out_dir.mkdir(parents=True, exist_ok=True)
    url = _source(url)
    for old in out_dir.glob("source.*"):
        old.unlink()
    cmd = ["yt-dlp", "--no-warnings", "--no-playlist",
           "-f", "bv*+ba/b", "-S", SORT,
           "--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts",
           "--merge-output-format", "mp4",
           # re-encode at the cut with high quality so the clip starts on a clean frame
           "--postprocessor-args", "ffmpeg:-c:v libx264 -crf 14 -preset medium -c:a aac -b:a 320k",
           "--paths", f"temp:{TMP}", "-o", str(out_dir / "source.%(ext)s"), url]
    if cookies_browser:
        cmd[1:1] = ["--cookies-from-browser", cookies_browser]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("yt-dlp failed: " + (p.stderr.strip().splitlines() or ["unknown error"])[-1])
    src = out_dir / "source.mp4"
    if not src.exists():
        found = list(out_dir.glob("source.*"))
        if not found:
            raise RuntimeError("yt-dlp finished but no clip file was written.")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(found[0]), "-c", "copy", str(src)], check=True)
        found[0].unlink()
    return src, video_info(src)


def best_format(url, cookies_browser=None):
    """What yt-dlp would pick (for the verification check)."""
    cmd = ["yt-dlp", "--no-warnings", "-f", "bv*+ba/b", "-S", SORT, "--print", "%(resolution)s %(fps)s", url]
    if cookies_browser:
        cmd[1:1] = ["--cookies-from-browser", cookies_browser]
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
