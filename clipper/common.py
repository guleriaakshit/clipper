"""Shared paths, the folder check, small helpers."""
import json
import os
import subprocess
import sys
from pathlib import Path

# The install folder: $CLIPPER_HOME, else the folder this package sits in.
ROOT = Path(os.environ.get("CLIPPER_HOME") or Path(__file__).resolve().parents[1])
JOBS = ROOT / "jobs"
TMP = ROOT / "tmp"
REMOTION = ROOT / "remotion"
MODELS = ROOT / "models"
CONFIG = ROOT / "config.json"          # written by install.sh; see config()


def config() -> dict:
    """Install choices: {"stt_model": dir or "", "giphy": bool, ...}. Missing file = defaults."""
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return {}


def require_home():
    """Make sure the Clipper folder is there and temp files stay inside it."""
    if not ROOT.is_dir():
        sys.exit(f"Clipper: folder {ROOT} not found. Set CLIPPER_HOME to the install folder.")
    TMP.mkdir(parents=True, exist_ok=True)
    JOBS.mkdir(parents=True, exist_ok=True)
    # keep every temp file (yt-dlp, ffmpeg, node, Chrome) in Clipper/tmp
    for k in ("TMPDIR", "TEMP", "TMP"):
        os.environ[k] = str(TMP)
    os.environ.setdefault("npm_config_cache", str(TMP / "npm-cache"))
    os.environ.setdefault("UV_CACHE_DIR", str(TMP / "uv-cache"))


def job_dir(platform: str, vid: str) -> Path:
    d = JOBS / f"{platform}-{vid}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def read_json(p, default=None):
    p = Path(p)
    if not p.exists():
        return default
    return json.loads(p.read_text())


def write_json(p, data):
    p = Path(p)
    tmp = p.with_suffix(p.suffix + ".part")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(p)


def set_status(jd: Path, stage: str, pct: float = 0, msg: str = "", error: str = ""):
    write_json(jd / "status.json", {"stage": stage, "pct": round(pct, 1), "msg": msg, "error": error})


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def ffprobe(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]).stdout
    return json.loads(out)


def video_info(path):
    """width, height, fps, duration of the first video stream."""
    j = ffprobe(path)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    if fps <= 0 or fps > 240:
        num, den = v["r_frame_rate"].split("/")
        fps = float(num) / float(den)
    return {"width": int(v["width"]), "height": int(v["height"]), "fps": round(fps, 3),
            "duration": float(j["format"]["duration"])}


def fmt_t(sec: float) -> str:
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


FONT_CANDIDATES = {
    "arial-black": ["/System/Library/Fonts/Supplemental/Arial Black.ttf", "/Library/Fonts/Arial Black.ttf",
                    "/usr/share/fonts/truetype/msttcorefonts/Arial_Black.ttf", "C:/Windows/Fonts/ariblk.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"],
    "impact": ["/System/Library/Fonts/Supplemental/Impact.ttf", "/Library/Fonts/Impact.ttf",
               "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf", "C:/Windows/Fonts/impact.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"],
}


def font(kind: str, size: int):
    """A PIL font: the first installed candidate for this kind, else Pillow's built-in font."""
    from PIL import ImageFont
    for f in FONT_CANDIDATES[kind]:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default(size)
