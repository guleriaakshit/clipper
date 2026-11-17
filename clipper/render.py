"""Clip -> Remotion props -> final 1080x1920 Short + SRT."""
import os
import re
import subprocess
from pathlib import Path

from .common import REMOTION, read_json, write_json
from . import subs

EDITABLE = ["trimStart", "trimEnd", "hookStart", "hookEnd", "hookEnabled", "layout", "split", "faceRect", "gameRect",
            "subsEnabled", "subColor", "subSize", "subY", "subUpper"]


def public_name(jd: Path, n: int) -> str:
    return f"clips/{jd.name}-{n}.mp4"


def link_source(jd: Path, n: int) -> str:
    """Hard-link the clip into remotion/public (same disk, no extra space)."""
    rel = public_name(jd, n)
    dst = REMOTION / "public" / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    src = jd / "clips" / str(n) / "source.mp4"
    if dst.exists():
        if os.path.samefile(src, dst):
            return rel
        dst.unlink()
    os.link(src, dst)
    return rel


def default_edit(clip, info, rects):
    split = 0.4
    return {
        "trimStart": 0.0, "trimEnd": round(info["duration"], 2),
        "hookStart": clip["hook"][0], "hookEnd": clip["hook"][1], "hookEnabled": True,
        # face found -> face band on top by default (flip to bottom in the dashboard); none found -> full-frame gameplay
        "layout": "faceTop" if rects.get("found") else "noFace",
        "split": split, "faceRect": rects["face"], "gameRect": rects["game"],
        "subsEnabled": True, "subColor": "#FFFFFF", "subSize": 110, "subY": split, "subUpper": True,
    }


def props_for(jd: Path, n: int):
    cd = jd / "clips" / str(n)
    meta = read_json(cd / "clip.json")
    edit = read_json(cd / "edit.json")
    words = read_json(cd / "words.json", [])
    info = meta["info"]
    p = {"src": link_source(jd, n), "srcWidth": info["width"], "srcHeight": info["height"],
         "fps": info["fps"], "clipLength": info["duration"], "words": words}
    p.update({k: edit[k] for k in EDITABLE})
    p["trimEnd"] = min(p["trimEnd"], info["duration"])
    return p


def timeline(p):
    segs, o = [], 0.0
    if p["hookEnabled"] and p["hookEnd"] > p["hookStart"]:
        segs.append((p["hookStart"], p["hookEnd"], 0.0))
        o = p["hookEnd"] - p["hookStart"]
    segs.append((p["trimStart"], min(p["trimEnd"], p["clipLength"]), o))
    return segs


def chrome_env():
    env = dict(os.environ)
    env["TMPDIR"] = subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True).stdout.strip() or "/tmp"
    env.pop("TEMP", None)
    env.pop("TMP", None)
    return env


def _lines(stream):
    buf = b""
    while True:
        ch = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
        if not ch:
            break
        buf += ch
        parts = re.split(rb"[\r\n]", buf)
        buf = parts.pop()
        for p in parts:
            if p.strip():
                yield p.decode("utf-8", "replace").rstrip()
    if buf.strip():
        yield buf.decode("utf-8", "replace")


def render(jd: Path, n: int, progress=lambda pct, msg: None):
    cd = jd / "clips" / str(n)
    p = props_for(jd, n)
    write_json(cd / "props.json", p)
    out = cd / "final.mp4"
    cmd = ["npx", "remotion", "render", "src/index.ts", "ClipShort", str(out), f"--props={cd / 'props.json'}",
           "--codec=h264", "--crf=16", "--pixel-format=yuv420p", "--color-space=bt709", "--audio-bitrate=320k",
           "--timeout=120000", "--log=info"]
    # Chrome builds a fresh profile in TMPDIR at every launch; on the USB stick that took >25 s and Remotion
    # gave up connecting. Use the laptop's per-user temp for this subprocess (small, cleaned by Remotion).
    proc = subprocess.Popen(cmd, cwd=REMOTION, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=chrome_env())
    tail = []
    for line in _lines(proc.stdout):           # Remotion redraws progress with \r, not \n
        tail = (tail + [line])[-30:]
        m = re.search(r"Rendered (\d+)/(\d+)", line)
        if m:
            progress(100 * int(m.group(1)) / int(m.group(2)), "Rendering")
        elif "Encoded" in line or "Stitch" in line:
            progress(99, "Encoding")
    if proc.wait() != 0:
        raise RuntimeError("Remotion render failed:\n" + "\n".join(tail[-10:]))
    if p["subsEnabled"] and p["words"]:
        subs.srt(p["words"], timeline(p), cd / "final.srt")
    return out
