"""Hand a raw clip to Claude Code: write clips/<n>/brief.md (everything Claude needs to edit the Short) and open
Claude Code in a new terminal with `/short <job> <n>` (the skill in skills/short). If Claude Code or a terminal
can't be opened, the dashboard shows the command to paste instead."""
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .common import JOBS, ROOT, fmt_t, read_json
from .render import timeline


def prompt(key: str, n: int) -> str:
    return f"/short {key} {n}"


def _transcript(words, gap=0.7, width=12):
    """words.json -> lines of '[12.3 s] text', a new line at each pause or every `width` words."""
    lines, cur, t0, last = [], [], None, None
    for w in words:
        if cur and (w["start"] - last > gap or len(cur) >= width):
            lines.append(f"[{t0:.1f} s] {' '.join(cur)}")
            cur = []
        if not cur:
            t0 = w["start"]
        cur.append(w["text"])
        last = w["end"]
    if cur:
        lines.append(f"[{t0:.1f} s] {' '.join(cur)}")
    return lines


def brief(key: str, n: int) -> Path:
    jd = JOBS / key
    cd = jd / "clips" / str(n)
    meta = read_json(cd / "clip.json")
    if not meta or not (cd / "source.mp4").exists():
        raise FileNotFoundError(f"Clip {n} of {key} isn't downloaded yet.")
    edit = read_json(cd / "edit.json", {}) or {}
    words = read_json(cd / "words.json", []) or []
    clips = read_json(jd / "clips.json", {}) or {}
    c = next((x for x in clips.get("clips", []) if x["id"] == n), {}) or {}
    info = meta["info"]
    hook = meta.get("hook") or [0, 0]
    p = {**edit, "clipLength": info["duration"]}
    hook_len = (p["hookEnd"] - p["hookStart"]) if p.get("hookEnabled") and p.get("hookEnd", 0) > p.get("hookStart", 0) else 0
    total = sum(b - a for a, b, _ in timeline(p)) if edit else info["duration"]
    rel = lambda f: f.relative_to(ROOT).as_posix()

    out = [f"# Clip {n} of {clips.get('title') or key}", "",
           f"Hand-off from the Clipper dashboard. Edit this clip into a Short with the /short skill: `{prompt(key, n)}`.", "",
           "## Files (paths from the Clipper folder)", "",
           f"- Raw clip: `{rel(cd / 'source.mp4')}` ({info['width']}x{info['height']}, {info['fps']:g} fps, "
           f"{info['duration']:.1f} s)",
           f"- Word timings: `{rel(cd / 'words.json')}` ({len(words)} words, seconds in the raw clip)",
           f"- Dashboard settings: `{rel(cd / 'edit.json')}` (crops, layout, hook, subtitle style)",
           f"- Effects: `{rel(cd / 'fx.json')}` ({'exists' if (cd / 'fx.json').exists() else 'not written yet'})",
           f"- Memes for this clip go in `{rel(cd / 'memes')}/`",
           f"- Render: `clipper render {key} {n}` -> `{rel(cd / 'final.mp4')}`", "",
           "## The moment", "",
           f"- Stream: {clips.get('title') or '?'} ({clips.get('channel') or c.get('channel') or 'channel unknown'})",
           f"- Starts at {c.get('vod_time') or fmt_t(meta.get('start', 0))} in the stream; score {c.get('score', '?')}/100"
           + (f", chat at {c['multiple']}x the stream's average" if c.get("multiple") else ""),
           f"- Found by: {' + '.join(c.get('basis', ['chat']))}",
           f"- Hottest moment (Clipper's cold-open pick): {hook[0]:.1f}-{hook[1]:.1f} s of the raw clip",
           f"- Face cam found: {'yes' if meta.get('face_found') else 'no'}; layout now `{edit.get('layout')}`, "
           f"face band {edit.get('split', 0):.2f} of the height",
           f"- Finished Short with the current settings: {total:.1f} s (hook {hook_len:.1f} s, then the body from "
           f"{edit.get('trimStart', 0):.1f} to {edit.get('trimEnd', info['duration']):.1f} s). "
           f"A body moment at s seconds plays at {hook_len:.1f} + s - {edit.get('trimStart', 0):.1f} in the Short."]
    if c.get("what"):
        out += [f"- What happens: {c['what']}"]
    if c.get("why"):
        out += [f"- Why it could work: {c['why']}"]
    if c.get("top_words"):
        out += [f"- Chat kept saying: {', '.join(c['top_words'][:10])}"]
    if c.get("signals"):
        out += ["- Chat reactions: " + ", ".join(f"{k} {v}" for k, v in c["signals"].items() if v)]
    if c.get("sample_chat"):
        out += ["", "## Chat at the spike", ""]
        for m in c["sample_chat"][:25]:
            out.append(f"- {m.get('user', '?')}: {m.get('text', m)}" if isinstance(m, dict) else f"- {m}")
    out += ["", "## What the streamer says (from words.json)", ""]
    out += _transcript(words) or ["(no words: no subtitles for this clip)"]
    f = cd / "brief.md"
    f.write_text("\n".join(out) + "\n", encoding="utf-8")
    return f


def claude_cli():
    return shutil.which("claude")


def open_claude(key: str, n: int) -> bool:
    """Start Claude Code in a new terminal window in the Clipper folder with /short <job> <n>. False if it couldn't."""
    exe = claude_cli()
    if not exe:
        return False
    text = prompt(key, n)
    try:
        if os.name == "nt":
            subprocess.Popen([exe, text], cwd=ROOT, creationflags=subprocess.CREATE_NEW_CONSOLE)
            return True
        line = f"cd {shlex.quote(str(ROOT))} && {shlex.quote(exe)} {shlex.quote(text)}"
        if sys.platform == "darwin":
            esc = line.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.Popen(["osascript", "-e", f'tell application "Terminal" to do script "{esc}"',
                              "-e", 'tell application "Terminal" to activate'])
            return True
        for term, args in (("x-terminal-emulator", ["-e"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]),
                           ("xfce4-terminal", ["-x"]), ("kitty", []), ("alacritty", ["-e"]), ("xterm", ["-e"])):
            if shutil.which(term):
                subprocess.Popen([term, *args, "bash", "-lc", line + "; exec bash"])
                return True
    except OSError:
        return False
    return False
