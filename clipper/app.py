"""Clipper dashboard (Gradio): find clips, review them, set up and render the vertical Short.
Run: ./clipper.sh serve  ->  http://localhost:8765"""
import html
import io
import subprocess
import threading
import time
from pathlib import Path

import gradio as gr
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import library, live, memes, pipeline, render
from .common import JOBS, REMOTION, TMP, read_json, write_json, fmt_t, font as font_of
from .platforms import detect

FACE_C, GAME_C = (63, 160, 255), (242, 162, 58)
LOCK = threading.Lock()          # one heavy job (analysis or render) at a time
STUDIO = {"proc": None}
PV_W, PV_H = 405, 720            # preview size (9:16)


# ---------------- data helpers ----------------
def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def job_choices():
    out = []
    for jd in sorted([p for p in JOBS.iterdir() if (p / "job.json").exists()], key=lambda p: p.stat().st_mtime, reverse=True):
        c = read_json(jd / "clips.json", {}) or {}
        k = len(c.get("clips", []))
        out.append((f"{c.get('title') or jd.name} ({k} clip{'' if k == 1 else 's'})", jd.name))
    return out


def clips_of(key):
    return (read_json(JOBS / key / "clips.json", {}) or {}).get("clips", []) if key else []


def clip_choices(key):
    return [(f"Clip {c['id']} at {c['vod_time']} (score {c['score']})", c["id"]) for c in clips_of(key)]


def clip_status(key, c):
    cd = JOBS / key / "clips" / str(c["id"])
    if (cd / "final.mp4").exists():
        return "rendered", "Rendered"
    if (cd / "clip.json").exists():
        return "ready", "Ready to edit"
    return "wait", "Downloading"


PLATFORM = {"twitch": "Twitch", "youtube": "YouTube", "kick": "Kick"}


def status_html(key):
    if not key:
        return ('<div class="np-status np-empty"><h2>No stream yet</h2>'
                '<p>Paste a past-stream link above and press <b>Find clips</b>. Clipper reads the chat replay, '
                'finds where it spiked and downloads only those parts.</p></div>')
    st = read_json(JOBS / key / "status.json", {}) or {}
    s = read_json(JOBS / key / "clips.json", {}) or {}
    job = read_json(JOBS / key / "job.json", {}) or {}
    title = esc(s.get("title") or key)
    plat = PLATFORM.get(s.get("platform") or job.get("platform"), "")
    sub = " on ".join(x for x in (esc(s.get("channel") or ""), plat) if x)
    head = f'<div class="np-st-title"><h2>{title}</h2>' + (f"<p>{sub}</p>" if sub else "") + "</div>"
    if st.get("error"):
        return (f'<div class="np-status">{head}<div class="np-note np-err"><b>This stream stopped.</b> '
                f'{esc(st["error"])}</div></div>')
    stage = (st.get("stage") or "").lower()
    if job.get("live"):
        on = live.running(JOBS / key)
        ok = [c for c in s.get("clips", [])]
        stats = [("Watched for" if not on else "Watching for", fmt_t(s.get("duration") or 0)),
                 ("Chat messages", f"{s.get('messages', 0):,}"),
                 ("Average chat", f"{float(s.get('avg_per_min') or 0):.0f}<small> / min</small>"),
                 ("Clips so far" if on else "Clips", str(len(ok)))]
        dl = "".join(f"<div><dt>{a}</dt><dd>{b}</dd></div>" for a, b in stats)
        badge = '<span class="np-live">Live</span>' if on else '<span class="np-live off">Ended</span>'
        head = head.replace("<h2>", f"<h2>{badge}", 1)
        return (f'<div class="np-status">{head}<dl class="np-stats">{dl}</dl>'
                f'<p class="np-livemsg">{esc(st.get("msg") or "")}</p></div>')
    if stage and stage != "done":
        pct = max(0.0, min(100.0, float(st.get("pct") or 0)))
        return (f'<div class="np-status">{head}<div class="np-run"><div class="np-run-line">'
                f'<b>{esc(stage.capitalize())}</b><span>{pct:.0f}%</span></div>'
                f'<div class="np-bar"><i style="width:{pct:.1f}%"></i></div>'
                f'<p>{esc(st.get("msg") or "")}</p></div></div>')
    stats = []
    if s.get("messages"):
        stats.append(("Chat messages", f"{s['messages']:,}"))
    if s.get("avg_per_min") is not None:
        stats.append(("Average chat", f"{s['avg_per_min']}<small> / min</small>"))
    if s.get("duration"):
        stats.append(("Stream length", fmt_t(s["duration"])))
    stats.append(("Clips found", str(len(s.get("clips", [])))))
    dl = "".join(f"<div><dt>{a}</dt><dd>{b}</dd></div>" for a, b in stats)
    return f'<div class="np-status">{head}<dl class="np-stats">{dl}</dl></div>'


def _tick_step(dur):
    for step in (30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800):
        if dur / step <= 8:
            return step
    return 14400


def chart_html(key, sel=None):
    r = read_json(JOBS / key / "rates.json", {}) if key else None
    if not r or not r.get("counts"):
        return ('<div class="np-chart np-chart-empty"><p>Chat activity shows here once a stream has been read.</p></div>')
    b, counts = float(r["bin"]), r["counts"]
    n = len(counts)
    dur = n * b
    k = max(1, round(30 / b))                      # 30 s rolling window, shown as messages per minute
    pm, run = [], 0.0
    for i, c in enumerate(counts):
        run += c
        if i >= k:
            run -= counts[i - k]
        pm.append(run / (min(i + 1, k) * b) * 60)
    cols = min(900, n)
    per = n / cols
    ys = [max(pm[int(i * per):max(int((i + 1) * per), int(i * per) + 1)]) for i in range(cols)]
    avg = float(r["avg"]) * 60 / b
    top = max(max(ys), 2 * avg) * 1.08 or 1.0

    def Y(v):
        return 100 - v / top * 100
    pts = [f"{(i + 0.5) / cols * 1000:.1f},{Y(v):.2f}" for i, v in enumerate(ys)]
    line = "M" + " L".join(pts)
    area = f"M0,100 L{' L'.join(pts)} L1000,100 Z"
    bands, flags = [], []
    for c in clips_of(key):
        x0 = max(0.0, c["start"] / dur * 1000)
        w = max(4.0, (c["end"] - c["start"]) / dur * 1000)
        on = " on" if sel is not None and c["id"] == sel else ""
        bands.append(f'<rect class="band{on}" x="{x0:.1f}" y="0" width="{w:.1f}" height="100"/>')
        mid = min(99.0, max(1.0, (x0 + w / 2) / 10))
        flags.append(f'<button class="np-flag{on}" data-clip="{c["id"]}" style="left:{mid:.2f}%" '
                     f'title="Clip {c["id"]} at {esc(c["vod_time"])}, {c["multiple"]}× average">{c["id"]}</button>')
    step = _tick_step(dur)
    ticks = "".join(f'<span style="left:{t / dur * 100:.2f}%">{fmt_t(t)}</span>'
                    for t in range(0, int(dur) + 1, step) if t / dur <= 0.97)
    ya, ys2 = Y(avg), Y(2 * avg)
    if (read_json(JOBS / key / "job.json", {}) or {}).get("live"):
        cap = ("Chat messages per minute since Clipper started watching. Shaded parts are the clips, numbered in the "
               "order they happened. Click a number to open that clip.")
    else:
        cap = ("Chat messages per minute across the whole stream. Shaded parts are the clips, numbered by rank. "
               "Click a number to open that clip.")
    return f"""<div class="np-chart">
  <div class="np-flags">{''.join(flags)}</div>
  <div class="np-plotwrap">
    <div class="np-plot">
      <svg viewBox="0 0 1000 100" preserveAspectRatio="none" aria-hidden="true">
        {''.join(bands)}
        <path class="area" d="{area}"/>
        <path class="trace" d="{line}"/>
        <line class="avg" x1="0" x2="1000" y1="{ya:.2f}" y2="{ya:.2f}"/>
        <line class="spk" x1="0" x2="1000" y1="{ys2:.2f}" y2="{ys2:.2f}"/>
      </svg>
    </div>
    <div class="np-ylab">
      <span class="spk" style="top:{ys2:.2f}%">Spike line {2 * avg:.0f}/min</span>
      <span class="avg" style="top:{ya:.2f}%">Average {avg:.0f}/min</span>
    </div>
  </div>
  <div class="np-ticks">{ticks}</div>
  <p class="np-cap">{cap}</p>
</div>"""


def list_html(key, sel=None):
    cs = clips_of(key)
    if not cs:
        return ('<div class="np-list np-list-empty"><p>No clips yet. They appear here, best first, as soon as the '
                'chat has been read.</p></div>')
    rows = []
    for c in cs:
        cls, label = clip_status(key, c)
        on = " on" if sel is not None and c["id"] == sel else ""
        what = (f'<p class="what">{esc(c["what"])}</p>' if c.get("what")
                else '<p class="what none">Not described yet. Run /clip in Claude Code to write what happens.</p>')
        rows.append(f"""<button class="np-row{on}" data-clip="{c['id']}">
  <span class="rank">{c['id']}</span>
  <span class="score"><b>{c['score']}</b><small>score</small></span>
  <span class="body">
    <span class="facts"><b>{c['multiple']}× average</b><span>at {esc(c['vod_time'])}</span><span>{round(c['end'] - c['start'])} s</span><span>{c['unique_chatters']} chatters</span></span>
    {what}
  </span>
  <span class="pill {cls}">{label}</span>
</button>""")
    return f'<div class="np-list">{"".join(rows)}</div>'


REACT = {"laugh": "Laughing", "hype": "Hype", "shock": "Shock", "clip": "“Clip it”", "sad": "Sad"}


def card_html(key, n, note=""):
    c = next((x for x in clips_of(key) if x["id"] == n), None)
    if not c:
        return ('<div class="np-card np-card-empty"><p>Pick a clip from the list or a number on the chart to see '
                'why it was picked.</p></div>')
    why = esc(c.get("why")) if c.get("why") else f'{esc(c["auto_reason"])} <em>Automatic summary. Run /clip for Claude\'s read.</em>'
    what = esc(c["what"]) if c.get("what") else "<em>Not written yet. Run /clip in Claude Code.</em>"
    sig = "".join(f'<span class="chip">{REACT.get(k, esc(k))} <b>{v}</b></span>' for k, v in c["signals"].items() if v) \
        or '<span class="muted">None picked up</span>'
    words = "".join(f'<span class="chip">{esc(w)}</span>' for w in c["top_words"][:8])
    facts = [("Peak", f"{c['peak_per_min']:.0f} msgs/min"), ("Stream average", f"{c['avg_per_min']:.0f} msgs/min"),
             ("Chatters", c["unique_chatters"]), ("Messages", c["messages"]),
             ("Starts at", esc(c["vod_time"])), ("Length", f"{round(c['end'] - c['start'])} s")]
    dl = "".join(f"<div><dt>{a}</dt><dd>{b}</dd></div>" for a, b in facts)
    return f"""<div class="np-card">
  <div class="np-card-top">
    <div class="np-score"><b>{c['score']}</b><span>/100</span></div>
    <div><p class="mult"><b>{c['multiple']}×</b> the stream's average chat</p><p class="muted">Clip {c['id']}, ranked by score</p></div>
  </div>
  <dl class="np-facts">{dl}</dl>
  <h4>What happens</h4><p>{what}</p>
  <h4>Why it could go viral</h4><p>{why}</p>
  <h4>Chat reactions</h4><div class="chips">{sig}</div>
  <h4>Chat kept saying</h4><div class="chips">{words}</div>
  {f'<p class="np-note">{note}</p>' if note else ''}
</div>"""


# ---------------- frames + preview ----------------
_FRAMES = {}


def grab(src: Path, t: float) -> Image.Image:
    k = (str(src), round(t, 1))
    if k not in _FRAMES:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t):.2f}", "-i", str(src), "-frames:v", "1",
                              "-vf", "scale=1280:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
        _FRAMES.clear() if len(_FRAMES) > 40 else None
        _FRAMES[k] = Image.open(io.BytesIO(raw)).convert("RGB") if raw else Image.new("RGB", (1280, 720))
    return _FRAMES[k]


def aspect(info, layout, split, kind):
    band_h = 1920 if layout == "noFace" else 1920 * (split if kind == "face" else 1 - split)
    return (1080 / band_h) / (info["width"] / info["height"])


def make_rect(x, y, w, a):
    """[x,y,w,h] (normalised) with h from the band aspect, kept inside the frame."""
    h = w / a
    k = max(w, h, 1.0)
    w, h = w / k, h / k
    return [round(min(max(x, 0), 1 - w), 4), round(min(max(y, 0), 1 - h), 4), round(w, 4), round(h, 4)]


def cover(frame, rect, bw, bh):
    W, H = frame.size
    rx, ry, rw, rh = rect
    cw, ch = rw * W, rh * H
    s = max(bw / cw, bh / ch)
    sw, sh = bw / s, bh / s
    sx, sy = rx * W + (cw - sw) / 2, ry * H + (ch - sh) / 2
    return frame.crop((int(sx), int(sy), int(sx + sw), int(sy + sh))).resize((bw, bh), Image.LANCZOS)


def word_at(words, t):
    return next((w["text"] for w in words if w["start"] <= t < w["end"]), None)


def preview(frame, e, words, t):
    out = Image.new("RGB", (PV_W, PV_H), "black")
    if e["layout"] == "noFace":
        out.paste(cover(frame, e["gameRect"], PV_W, PV_H), (0, 0))
    else:
        fh = round(PV_H * e["split"])
        face, game = cover(frame, e["faceRect"], PV_W, fh), cover(frame, e["gameRect"], PV_W, PV_H - fh)
        if e["layout"] == "faceTop":
            out.paste(face, (0, 0)); out.paste(game, (0, fh))
        else:
            out.paste(game, (0, 0)); out.paste(face, (0, PV_H - fh))
    w = word_at(words, t) if e["subsEnabled"] else None
    if e["subsEnabled"] and not w:
        w = "preview"                     # show the style even between words
    if w:
        txt = w.upper() if e["subUpper"] else w
        size = int(e["subSize"] * PV_W / 1080)
        font = font_of("arial-black", size)
        cx, cy = PV_W // 2, int(PV_H * e["subY"])
        shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(shadow)
        d.text((cx, cy + size * 0.05), txt, font=font, fill=(0, 0, 0, 230), anchor="mm")
        shadow = shadow.filter(ImageFilter.GaussianBlur(size * 0.12))
        out = Image.alpha_composite(out.convert("RGBA"), shadow)
        d = ImageDraw.Draw(out)
        for dx, dy in ((0, 0.055), (0.035, 0.035), (-0.018, 0.018)):
            d.text((cx + size * dx, cy + size * dy), txt, font=font, fill=(0, 0, 0, 255), anchor="mm")
        d.text((cx, cy), txt, font=font, fill=e["subColor"], anchor="mm")
        out = out.convert("RGB")
    return out


def boxes(frame, e):
    img = frame.copy()
    d = ImageDraw.Draw(img)
    W, H = img.size
    kinds = [("gameRect", GAME_C, "GAMEPLAY")] + ([] if e["layout"] == "noFace" else [("faceRect", FACE_C, "FACE CAM")])
    for key, col, label in kinds:
        x, y, w, h = e[key]
        d.rectangle([x * W, y * H, (x + w) * W, (y + h) * H], outline=col, width=5)
        d.rectangle([x * W, max(0, y * H - 26), x * W + 120, max(26, y * H)], fill=col)
        d.text((x * W + 6, max(0, y * H - 24)), label, fill=(14, 14, 16), font=font_of("arial-black", 16))
    return img


# ---------------- actions ----------------
def start_analysis(url, top_n, cookies, chat_file, hours):
    try:
        target = live.probe(url or "")
    except live.NotLive as e:
        raise gr.Error(str(e))
    except Exception as e:
        raise gr.Error(f"Couldn't check that link: {e}")
    if target:
        jd = live.new_job(target, hours=float(hours or 6))
        live.start_process(jd)
        gr.Info(f"Watching {target.get('channel') or target['id']} live. Clips appear as chat spikes.")
        return gr.update(choices=job_choices(), value=jd.name)
    try:
        platform, vid = detect(url or "")
    except ValueError as e:
        raise gr.Error("Couldn't read that link. Use a past stream (Twitch VOD, YouTube stream, Kick VOD) or a "
                       "stream that's live now (Twitch channel link or live YouTube link).")
    key = f"{platform}-{vid}"
    jd = JOBS / key
    jd.mkdir(parents=True, exist_ok=True)
    write_json(jd / "job.json", {"url": url, "platform": platform, "id": vid})
    write_json(jd / "status.json", {"stage": "queued", "pct": 0, "msg": "Waiting to start", "error": ""})
    cf = None
    if chat_file:
        src = Path(chat_file if isinstance(chat_file, str) else chat_file.name)
        cf = jd / ("chat_upload" + src.suffix.lower())
        cf.write_bytes(src.read_bytes())

    def work():
        with LOCK:
            try:
                pipeline.analyze(url, top_n=int(top_n), cookies_browser=cookies or None, chat_file=cf)
            except Exception:
                pass                       # the error is in status.json
    threading.Thread(target=work, daemon=True).start()
    return gr.update(choices=job_choices(), value=key)


def _stop_btn(key):
    return gr.update(visible=bool(key) and live.running(JOBS / key))


def refresh_job(key, sel=None):
    return (status_html(key), chart_html(key, sel), list_html(key, sel), gr.update(choices=clip_choices(key)),
            _stop_btn(key))


def switch_job(key):
    """New stream picked: fresh view, no clip open."""
    return (status_html(key), chart_html(key), list_html(key), gr.update(choices=clip_choices(key), value=None),
            _stop_btn(key))


def stop_live(key):
    if key:
        live.stop(JOBS / key)
        gr.Info("Stopping. Clips already cut stay.")
    return gr.update(visible=False)


_SETTLED = {}


def tick(key, sel):
    """Timer: refresh while a job runs; stay quiet once it's finished."""
    st = read_json(JOBS / key / "status.json", {}) if key else {}
    sig = ((st or {}).get("stage"), (st or {}).get("pct"), (st or {}).get("msg"),
           (JOBS / key / "clips.json").stat().st_mtime if key and (JOBS / key / "clips.json").exists() else 0)
    if not key or _SETTLED.get(key) == sig:
        return gr.skip(), gr.skip(), gr.skip(), gr.skip(), gr.skip()
    if sig[0] in ("done", "error"):
        _SETTLED[key] = sig
    return refresh_job(key, sel)


def first_load(key):
    _SETTLED.pop(key, None)
    return refresh_job(key)


def pick_clip(evt: gr.EventData):
    n = getattr(evt, "clip", None)
    if n is None:
        n = (getattr(evt, "_data", None) or {}).get("clip")
    return gr.skip() if n is None else int(n)


def highlight(key, n):
    return chart_html(key, n), list_html(key, n)


def load_clip(key, n):
    """Fill the card + editor for one clip."""
    empty = [gr.update()] * 19 + [gr.update(value=None, visible=False)] * 2   # 19 editor fields + final video + files
    if not key or n is None:
        return [card_html(key, None), gr.update(value=None, visible=False)] + empty
    cd = JOBS / key / "clips" / str(n)
    if not (cd / "clip.json").exists():
        return [card_html(key, n, "This clip is still downloading. The editor opens when it is ready."),
                gr.update(value=None, visible=False)] + empty
    meta, e = read_json(cd / "clip.json"), read_json(cd / "edit.json")
    dur = meta["info"]["duration"]
    final = str(cd / "final.mp4") if (cd / "final.mp4").exists() else None
    files = [str(p) for p in (cd / "final.mp4", cd / "final.srt") if p.exists()] or None
    return [card_html(key, n), gr.update(value=str(cd / "source.mp4"), visible=True),
            gr.update(maximum=round(dur, 1), value=round(min(e["hookStart"] + 1, dur - 0.1), 1)),
            e["layout"], e["split"], e["faceRect"][0], e["faceRect"][1], e["faceRect"][2],
            e["gameRect"][0], e["gameRect"][1], e["gameRect"][2],
            e["hookEnabled"], e["hookStart"], e["hookEnd"], e["trimStart"], e["trimEnd"],
            e["subsEnabled"], e["subColor"], e["subSize"], e["subY"], e["subUpper"],
            gr.update(value=final, visible=bool(final)), gr.update(value=files, visible=bool(files))]


def apply_edit(key, n, t, layout, split, fx, fy, fw, gx, gy, gw, hook_on, hs, he, ts, te, subs_on, color, size, sy, upper):
    """Save settings, return the crop view and the 9:16 preview."""
    if not key or n is None:
        return None, None
    cd = JOBS / key / "clips" / str(n)
    if not (cd / "clip.json").exists():
        return None, None
    meta, e = read_json(cd / "clip.json"), read_json(cd / "edit.json")
    info, dur = meta["info"], meta["info"]["duration"]
    e.update({"layout": layout, "split": float(split),
              "faceRect": make_rect(fx, fy, fw, aspect(info, layout, split, "face")),
              "gameRect": make_rect(gx, gy, gw, aspect(info, layout, split, "game")),
              "hookEnabled": bool(hook_on), "hookStart": max(0.0, min(float(hs), dur)), "hookEnd": max(0.0, min(float(he), dur)),
              "trimStart": max(0.0, min(float(ts), dur)), "trimEnd": max(0.0, min(float(te), dur)),
              "subsEnabled": bool(subs_on), "subColor": _hex(color), "subSize": int(size), "subY": float(sy), "subUpper": bool(upper)})
    write_json(cd / "edit.json", e)
    frame = grab(cd / "source.mp4", float(t))
    return boxes(frame, e), preview(frame, e, read_json(cd / "words.json", []), float(t))


def _hex(c):
    c = (c or "#FFFFFF").strip()
    if c.startswith("rgb"):
        nums = [int(float(v)) for v in c[c.index("(") + 1:c.index(")")].split(",")[:3]]
        return "#%02X%02X%02X" % tuple(nums)
    return c.upper()


def snap_seam(layout, split):
    return round(1 - split, 2) if layout == "faceBottom" else round(split, 2) if layout == "faceTop" else 0.75


def do_render(key, n):
    if not key or n is None:
        raise gr.Error("Pick a clip first.")
    jd, cd = JOBS / key, JOBS / key / "clips" / str(n)
    state = {"pct": 0, "msg": "Waiting for the renderer", "err": None, "done": False}

    def work():
        with LOCK:
            try:
                render.render(jd, n, lambda p, m: state.update(pct=p, msg=m))
            except Exception as ex:
                state["err"] = str(ex)[-1500:]
            state["done"] = True
    threading.Thread(target=work, daemon=True).start()
    while not state["done"]:
        yield progress_html("Rendering", state["pct"], state["msg"]), gr.update(), gr.update()
        time.sleep(1)
    if state["err"]:
        yield (f'<div class="np-note np-err"><b>The render stopped.</b> The renderer said:<pre>{esc(state["err"])}</pre>'
               f'</div>'), gr.update(), gr.update()
        return
    files = [str(p) for p in (cd / "final.mp4", cd / "final.srt") if p.exists()]
    yield '<div class="np-note np-ok"><b>Rendered.</b> The Short and its subtitles are ready to download below.</div>', \
        gr.update(value=str(cd / "final.mp4"), visible=True), gr.update(value=files, visible=True)


def progress_html(label, pct, msg=""):
    pct = max(0.0, min(100.0, float(pct or 0)))
    return (f'<div class="np-run"><div class="np-run-line"><b>{esc(label)}</b><span>{pct:.0f}%</span></div>'
            f'<div class="np-bar"><i style="width:{pct:.1f}%"></i></div><p>{esc(msg)}</p></div>')


def open_studio(key, n):
    if not key or n is None:
        raise gr.Error("Pick a clip first.")
    cd = JOBS / key / "clips" / str(n)
    write_json(cd / "props.json", render.props_for(JOBS / key, n))
    if STUDIO["proc"] and STUDIO["proc"].poll() is None:
        STUDIO["proc"].terminate()
    STUDIO["proc"] = subprocess.Popen(["npx", "remotion", "studio", "src/index.ts", f"--props={cd / 'props.json'}",
                                       "--port=3000"], cwd=REMOTION, env=render.chrome_env(), stdout=open(TMP / "studio.log", "w"),
                                      stderr=subprocess.STDOUT)
    return ('<div class="np-note">Remotion Studio is starting. It opens at '
            '<a href="http://localhost:3000/ClipShort" target="_blank">localhost:3000/ClipShort</a> in a few seconds.</div>')


# ---------------- memes: video memes with sound, meme sounds, classic images ----------------
def _meme_outputs(kind=None, image=None, video=None, audio=None):
    """Preview slots: image (classic), video (video memes), audio (sounds); only the one in use is shown."""
    return (gr.update(value=image, visible=kind == "image"), gr.update(value=video, visible=kind == "video"),
            gr.update(value=audio, visible=kind == "sound"))


def meme_load(key, n):
    """Memes already saved for this clip + a search idea from what chat kept saying."""
    c = next((x for x in clips_of(key) if x["id"] == n), None)
    if not c:
        return gr.update(), [], gr.update(value=None, visible=False)
    cd = JOBS / key / "clips" / str(n)
    got = memes.saved(cd)
    return " ".join(c.get("top_words", [])[:2]), memes.saved_visual(cd), gr.update(value=got or None, visible=bool(got))


def meme_search(q, source):
    try:
        rows = memes.search(q or "", source)
    except memes.MemeError as ex:
        raise gr.Error(str(ex))
    except Exception as ex:
        raise gr.Error(f"Search failed: {ex}")
    if not rows:
        gr.Info("Nothing found. Try another word.")
    sounds = source == "sounds"
    gal = [(r["preview"], f"{r['name']} ({r['duration']:.0f} s)" if r.get("duration") else r["name"])
           for r in rows if r.get("preview")]
    return (gr.update(value=None if sounds else gal, visible=not sounds),
            gr.update(choices=[(r["name"], i) for i, r in enumerate(rows)] if sounds else [], value=None, visible=sounds),
            rows, None, "Click a result to pick it.", gr.update(visible=False), *_meme_outputs())


def _picked(r):
    if not r:
        return None, "Click a result to pick it.", gr.update(visible=False)
    src = {"YouTube": "a YouTube meme clip", "Myinstants": "myinstants.com"}.get(r["source"], r["source"])
    info = f"**{r['name']}**  \nFrom {src}" + (f", [open its page]({r['page']})" if r.get("page") else "")
    return r, info, gr.update(visible=r["kind"] == "image")


def meme_pick(rows, evt: gr.SelectData):
    vis = [r for r in rows if r.get("preview")]
    return _picked(vis[evt.index] if vis and evt.index < len(vis) else None)


def meme_pick_sound(rows, i):
    return _picked(rows[int(i)] if rows and i is not None and int(i) < len(rows) else None)


def meme_preview(item, top, bottom):
    if not item:
        return _meme_outputs()
    try:
        if item["kind"] == "image":
            return _meme_outputs("image", image=memes.captioned(item, top or "", bottom or ""))
        f = str(memes.blank(item))          # downloads the clip/sound once, then it's cached
        return _meme_outputs(item["kind"], video=f if item["kind"] == "video" else None,
                             audio=f if item["kind"] == "sound" else None)
    except Exception as ex:
        raise gr.Error(f"Preview failed: {ex}")


def meme_save(key, n, item, top, bottom):
    if not key or n is None:
        raise gr.Error("Open a clip first (Clips tab).")
    if not item:
        raise gr.Error("Click a meme in the results first.")
    cd = JOBS / key / "clips" / str(n)
    try:
        f = memes.save(cd, item, top or "", bottom or "")
    except Exception as ex:
        raise gr.Error(f"Save failed: {ex}")
    got = memes.saved(cd)
    return (f"Saved `{Path(f).name}` in `clips/{n}/memes/`.", memes.saved_visual(cd),
            gr.update(value=got, visible=True))


# ---------------- library: delete streams and clips ----------------
def _trash_note(n=2):
    if not library.TRASH.is_dir():
        return "This can't be undone."
    return "It goes to the Trash, so you can still get it back." if n == 1 else \
        "They go to the Trash, so you can still get them back."


def lib_stream_choices():
    out = []
    for jd in sorted([p for p in JOBS.iterdir() if (p / "job.json").exists()], key=lambda p: p.stat().st_mtime, reverse=True):
        c = read_json(jd / "clips.json", {}) or {}
        k = len(c.get("clips", []))
        tag = ", watching live" if live.running(jd) else ""
        out.append((f"{c.get('title') or jd.name} ({k} clip{'' if k == 1 else 's'}, "
                    f"{library.human(library.size_of(jd))}{tag})", jd.name))
    return out


def lib_clip_choices(key):
    out = []
    for c in clips_of(key):
        cd = JOBS / key / "clips" / str(c["id"])
        out.append((f"Clip {c['id']} at {c['vod_time']}, score {c['score']} ({library.human(library.size_of(cd))})",
                    c["id"]))
    return out


def lib_refresh(key):
    return (gr.update(choices=lib_stream_choices(), value=[]), gr.update(choices=lib_clip_choices(key), value=[]),
            gr.update(visible=False), gr.update(visible=False))


def ask_streams(keys):
    if not keys:
        raise gr.Error("Tick at least one stream first.")
    size = sum(library.size_of(JOBS / k) for k in keys)
    return (f'<div class="np-note np-err"><b>Delete {len(keys)} stream{"" if len(keys) == 1 else "s"} and all their '
            f'clips ({library.human(size)})?</b> {_trash_note(len(keys))}</div>', gr.update(visible=True))


def ask_clips(key, ids):
    if not key or not ids:
        raise gr.Error("Tick at least one clip first.")
    size = sum(library.size_of(JOBS / key / "clips" / str(i)) for i in ids)
    return (f'<div class="np-note np-err"><b>Delete {len(ids)} clip{"" if len(ids) == 1 else "s"} '
            f'({library.human(size)})?</b> {_trash_note(len(ids))}</div>', gr.update(visible=True))


def _result(done, skipped, what):
    msg = f"Deleted {len(done)} {what}{'' if len(done) == 1 else 's'}." if done else ""
    if skipped:
        msg += " Skipped " + ", ".join(f"{k}: {why}" for k, why in skipped) + "."
    return f'<div class="np-note{" np-ok" if done and not skipped else ""}">{esc(msg.strip())}</div>'


def do_delete_streams(keys, cur):
    done, skipped = library.delete_streams(keys or [])
    choices = job_choices()
    new = cur if cur not in done else (choices[0][1] if choices else None)
    return (gr.update(choices=choices, value=new), gr.update(value=None) if cur in done else gr.update(),
            _result(done, skipped, "stream"), *lib_refresh(new))


def do_delete_clips(key, ids, cur_clip):
    done, skipped = library.delete_clips(key, ids or [])
    gone = cur_clip is not None and cur_clip in done
    return (gr.update(choices=clip_choices(key), value=None if gone else cur_clip), list_html(key, None if gone else cur_clip),
            chart_html(key, None if gone else cur_clip), _result(done, skipped, "clip"), *lib_refresh(key))


# ---------------- UI ----------------
# Look: cool light grey ground, white sheets, ink text, one "heat" colour for spikes and scores.
# Instrument Sans throughout (condensed width for big numbers). Nothing moves: no animations or transitions.
FONT_URL = "https://fonts.googleapis.com/css2?family=Instrument+Sans:wdth,wght@75..100,400..700&display=swap"
HEAD = (f'<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        f'<link rel="stylesheet" href="{FONT_URL}"><meta name="color-scheme" content="light">')
FORCE_LIGHT = "() => { document.body.classList.remove('dark'); document.documentElement.classList.remove('dark'); }"

INK, MUTED, RULE, SHEET, SUNK, GROUND, HEAT = "#181B21", "#646C79", "#D9DDE3", "#FFFFFF", "#F5F6F8", "#ECEEF1", "#E5432E"

THEME = gr.themes.Base(
    primary_hue=gr.themes.colors.slate, neutral_hue=gr.themes.colors.slate, radius_size=gr.themes.sizes.radius_sm,
    spacing_size=gr.themes.sizes.spacing_md, text_size=gr.themes.sizes.text_md,
    font=[gr.themes.Font(f) for f in ("Instrument Sans", "ui-sans-serif", "system-ui", "sans-serif")],
    font_mono=[gr.themes.Font(f) for f in ("ui-monospace", "SFMono-Regular", "Menlo", "monospace")],
).set(
    body_background_fill=GROUND, body_text_color=INK, body_text_color_subdued=MUTED,
    background_fill_primary=SHEET, background_fill_secondary=SUNK,
    border_color_primary=RULE, border_color_accent=INK, color_accent=INK, color_accent_soft=SUNK,
    block_background_fill=SHEET, block_border_color=RULE, block_border_width="1px", block_shadow="none",
    block_radius="10px", block_padding="14px", block_label_background_fill="transparent",
    block_label_text_color=MUTED, block_label_text_size="13px", block_label_text_weight="500",
    block_label_border_width="0px", block_label_padding="0", block_label_margin="0",
    block_title_text_color=INK, block_title_text_size="13px", block_title_text_weight="600",
    block_title_background_fill="transparent", block_title_padding="0",
    panel_background_fill=SHEET, panel_border_color=RULE, panel_border_width="1px",
    input_background_fill=SUNK, input_background_fill_focus=SHEET, input_border_color=RULE,
    input_border_color_focus=INK, input_border_width="1px", input_shadow="none", input_shadow_focus="none",
    input_radius="8px", input_padding="10px 12px", input_text_size="15px", input_placeholder_color="#9AA1AD",
    button_primary_background_fill=INK, button_primary_background_fill_hover="#2C313B",
    button_primary_text_color="#FFFFFF", button_primary_border_color=INK, button_primary_border_color_hover="#2C313B",
    button_secondary_background_fill=SHEET, button_secondary_background_fill_hover=SUNK,
    button_secondary_text_color=INK, button_secondary_border_color=RULE, button_secondary_border_color_hover="#B9BFC9",
    button_border_width="1px", button_large_radius="8px", button_medium_radius="8px", button_small_radius="6px",
    button_large_text_size="15px", button_large_text_weight="600", button_medium_text_weight="600",
    button_small_text_weight="500", button_primary_shadow="none", button_primary_shadow_hover="none",
    button_primary_shadow_active="none", button_secondary_shadow="none", button_secondary_shadow_hover="none",
    button_secondary_shadow_active="none",
    button_transition="none",
    slider_color=INK, checkbox_background_color=SHEET, checkbox_background_color_selected=INK,
    checkbox_border_color=RULE, checkbox_border_color_selected=INK, checkbox_border_color_focus=INK,
    checkbox_label_background_fill=SHEET, checkbox_label_background_fill_selected=SUNK,
    checkbox_label_border_color=RULE, checkbox_label_border_color_selected=INK, checkbox_label_text_color_selected=INK,
    checkbox_label_shadow="none", checkbox_shadow="none",
    table_border_color=RULE, table_even_background_fill=SHEET, table_odd_background_fill=SUNK,
    link_text_color=INK, link_text_color_hover=INK, link_text_color_visited=INK,
    shadow_drop="none", shadow_drop_lg="0 6px 24px rgba(24,27,33,.10)", shadow_spread="0px",
    layout_gap="14px", form_gap_width="0px",
    loader_color=MUTED, stat_background_fill=SUNK,
)

CSS = """
/* nothing moves */
*, *::before, *::after { animation: none !important; transition: none !important; scroll-behavior: auto !important; }
.eta-bar, .progress-level-inner + .progress-bar { display: none !important; }

:root { --ink:#181B21; --muted:#646C79; --rule:#D9DDE3; --sheet:#FFFFFF; --sunk:#F5F6F8; --ground:#ECEEF1;
        --heat:#E5432E; --heat-soft:#FCE6E2; --ok:#1E8A52; --ok-soft:#E3F3EA; --wait:#9A6B00; --wait-soft:#FBF1D9; }
body, .gradio-container { background: var(--ground) !important; font-feature-settings: "tnum" 1; }
.gradio-container { max-width: 1360px !important; margin: 0 auto !important; padding: 28px 32px 48px !important; }
footer { display: none !important; }
:focus-visible { outline: 2px solid var(--ink) !important; outline-offset: 2px; }
.prose, .prose p { font-size: 15px; line-height: 1.55; }

.html-container { padding: 0 !important; }

/* masthead */
#np-mast { align-items: flex-start; gap: 32px; margin: 0 0 8px; }
#np-brand h1 { font-size: 40px; line-height: 1; font-weight: 700; font-stretch: 75%; letter-spacing: -0.01em; margin: 0 0 8px; color: var(--ink); }
#np-brand p { margin: 0; color: var(--muted); font-size: 15px; max-width: 44ch; }
#np-find { gap: 10px; align-items: stretch; margin-top: 4px; }
#np-find button { min-height: 48px; }
#np-url, #np-url label, #np-url .input-container { height: 100%; }
#np-url input, #np-url textarea { font-size: 15px; height: 48px; background: var(--sheet) !important; border-color: #C9CED6 !important; }
#np-url input:focus { border-color: var(--ink) !important; }
#np-opts { background: transparent !important; border: 0 !important; padding: 6px 0 0 !important; }
#np-opts > button, #np-opts .label-wrap { padding: 4px 0 !important; justify-content: flex-start !important; gap: 6px; width: auto !important; }
#np-opts .label-wrap span, #np-opts > button span { font-size: 13.5px; color: var(--muted); font-weight: 500; }
#np-opts .icon { order: -1; margin: 0 !important; }

/* stream bar */
#np-bar { align-items: stretch; }
.np-status { padding: 2px 2px 0; }
.np-status h2 { font-size: 26px; line-height: 1.15; font-weight: 700; font-stretch: 85%; margin: 0; color: var(--ink); overflow-wrap: anywhere; }
.np-st-title p { margin: 4px 0 0; color: var(--muted); font-size: 14px; }
.np-empty p { color: var(--muted); max-width: 64ch; margin: 6px 0 0; font-size: 15px; }
.np-stats { display: flex; flex-wrap: wrap; gap: 0; margin: 18px 0 0; padding: 0; }
.np-stats > div { padding: 0 28px 0 0; margin-right: 28px; border-right: 1px solid var(--rule); }
.np-stats > div:last-child { border-right: 0; }
.np-stats dt { font-size: 13px; color: var(--muted); margin: 0 0 2px; }
.np-stats dd { margin: 0; font-size: 24px; font-weight: 650; font-stretch: 80%; color: var(--ink); }
.np-stats dd small { font-size: 14px; font-weight: 500; color: var(--muted); font-stretch: 100%; }

/* progress (static) */
.np-run { margin-top: 16px; max-width: 560px; }
.np-run-line { display: flex; justify-content: space-between; font-size: 14px; margin-bottom: 6px; }
.np-run-line span { color: var(--muted); }
.np-bar { height: 6px; background: var(--sunk); border: 1px solid var(--rule); border-radius: 99px; overflow: hidden; }
.np-bar i { display: block; height: 100%; background: var(--ink); }
.np-run p { margin: 8px 0 0; color: var(--muted); font-size: 14px; }

/* notes */
.np-note { margin: 14px 0 0; padding: 10px 14px; border-radius: 8px; background: var(--sunk); border: 1px solid var(--rule); font-size: 14px; line-height: 1.5; color: var(--ink); }
.np-note.np-err { background: var(--heat-soft); border-color: #F2B8AE; }
.np-note.np-ok { background: var(--ok-soft); border-color: #B5DEC6; }
.np-note pre { white-space: pre-wrap; font-size: 12px; margin: 8px 0 0; max-height: 220px; overflow: auto; }
.np-note a { color: var(--ink); text-decoration: underline; text-underline-offset: 2px; }

/* the chart: the one loud thing */
#np-chartbox { padding: 18px 20px 14px !important; }
.np-chart { position: relative; }
.np-chart-empty { height: 180px; display: grid; place-items: center; background: var(--sunk); border-radius: 8px; }
.np-chart-empty p { color: var(--muted); margin: 0; font-size: 14px; }
.np-flags { position: relative; height: 30px; margin-right: 128px; }
.np-flag { position: absolute; top: 0; transform: translateX(-50%); min-width: 26px; height: 26px; padding: 0 7px;
  border-radius: 6px 6px 6px 0; background: var(--sheet); color: var(--heat); border: 1.5px solid var(--heat);
  font: 700 14px/22px "Instrument Sans", sans-serif; font-stretch: 80%; cursor: pointer; }
.np-flag:hover, .np-flag.on { background: var(--heat); color: #fff; }
.np-plotwrap { display: flex; }
.np-plot { position: relative; flex: 1; height: 200px; border-bottom: 1px solid var(--ink); }
.np-plot svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
.np-plot .area { fill: #E3E6EB; }
.np-plot .trace { fill: none; stroke: var(--ink); stroke-width: 1.25; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
.np-plot .band { fill: var(--heat); fill-opacity: .14; }
.np-plot .band.on { fill-opacity: .30; }
.np-plot line.avg { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 2 3; vector-effect: non-scaling-stroke; }
.np-plot line.spk { stroke: var(--heat); stroke-width: 1.25; stroke-dasharray: 6 4; vector-effect: non-scaling-stroke; }
.np-ylab { position: relative; width: 128px; flex: none; }
.np-ylab span { position: absolute; left: 10px; transform: translateY(-50%); font-size: 12.5px; white-space: nowrap; }
.np-ylab .spk { color: var(--heat); font-weight: 600; }
.np-ylab .avg { color: var(--muted); }
.np-ticks { position: relative; height: 22px; margin-right: 128px; }
.np-ticks span { position: absolute; top: 6px; font-size: 12.5px; color: var(--muted); transform: translateX(-50%); }
.np-ticks span:first-child { transform: none; }
.np-cap { margin: 8px 0 0; font-size: 13px; color: var(--muted); }

/* tabs */
.tab-wrapper, [role="tablist"] { border-bottom: 1px solid var(--rule) !important; margin-top: 10px; }
button[role="tab"] { font-size: 15px !important; font-weight: 600 !important; color: var(--muted) !important; padding: 10px 2px !important; margin-right: 22px !important; background: transparent !important; border: 0 !important; }
button[role="tab"].selected, button[role="tab"][aria-selected="true"] { color: var(--ink) !important; }
button[role="tab"].selected::after, button[role="tab"][aria-selected="true"]::after { background: var(--ink) !important; height: 2px !important; }
.tabitem { padding: 16px 0 0 !important; border: 0 !important; background: transparent !important; }

/* clip list */
.np-list { display: flex; flex-direction: column; background: var(--sheet); border: 1px solid var(--rule); border-radius: 10px; overflow: hidden; }
.np-list-empty { padding: 28px 20px; color: var(--muted); }
.np-list-empty p { margin: 0; font-size: 15px; }
.np-row { all: unset; box-sizing: border-box; display: grid; grid-template-columns: 34px 58px minmax(0,1fr) auto; gap: 14px; align-items: start;
  padding: 16px 18px 16px 15px; border-left: 3px solid transparent; border-bottom: 1px solid var(--rule); cursor: pointer; }
.np-row:last-child { border-bottom: 0; }
.np-row:hover { background: var(--sunk); }
.np-row.on { background: var(--sunk); border-left-color: var(--heat); }
.np-row:focus-visible { outline: 2px solid var(--ink); outline-offset: -2px; }
.np-row .rank { font-size: 26px; line-height: 1; font-weight: 700; font-stretch: 75%; color: var(--ink); padding-top: 2px; }
.np-row .score { display: flex; flex-direction: column; line-height: 1; }
.np-row .score b { font-size: 26px; font-weight: 700; font-stretch: 75%; color: var(--heat); }
.np-row .score small { font-size: 12px; color: var(--muted); margin-top: 4px; }
.np-row .facts { display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: 14px; color: var(--muted); }
.np-row .facts b { color: var(--ink); font-weight: 600; }
.np-row .what { margin: 6px 0 0; font-size: 14.5px; line-height: 1.45; color: var(--ink); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.np-row .what.none { color: #8D94A0; }
.pill { font-size: 12.5px; font-weight: 600; padding: 3px 9px; border-radius: 99px; white-space: nowrap; }
.pill.rendered { background: var(--ok-soft); color: var(--ok); }
.pill.ready { background: var(--sunk); color: var(--ink); border: 1px solid var(--rule); }
.pill.wait { background: var(--wait-soft); color: var(--wait); }

/* clip card */
.np-card { background: var(--sheet); border: 1px solid var(--rule); border-radius: 10px; padding: 20px 22px 22px; }
.np-card-empty { color: var(--muted); padding: 28px 22px; }
.np-card-empty p { margin: 0; font-size: 15px; }
.np-card-top { display: flex; gap: 18px; align-items: center; padding-bottom: 16px; border-bottom: 1px solid var(--rule); }
.np-score { display: flex; align-items: baseline; }
.np-score b { font-size: 64px; line-height: .9; font-weight: 700; font-stretch: 75%; color: var(--heat); letter-spacing: -0.02em; }
.np-score span { font-size: 16px; color: var(--muted); margin-left: 3px; }
.np-card .mult { margin: 0; font-size: 18px; color: var(--ink); }
.np-card .mult b { font-weight: 700; }
.np-card .muted, .muted { color: var(--muted); margin: 2px 0 0; font-size: 14px; }
.np-facts { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 12px 16px; margin: 16px 0 4px; }
.np-facts dt { font-size: 12.5px; color: var(--muted); }
.np-facts dd { margin: 1px 0 0; font-size: 16px; font-weight: 600; color: var(--ink); }
.np-card h4 { font-size: 13px; font-weight: 600; color: var(--muted); margin: 18px 0 5px; }
.np-card p { margin: 0; font-size: 15px; line-height: 1.55; max-width: 70ch; }
.np-card em { color: var(--muted); font-style: normal; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font-size: 13px; padding: 3px 9px; border-radius: 6px; background: var(--sunk); border: 1px solid var(--rule); color: var(--ink); }
.chip b { font-weight: 700; margin-left: 2px; }

/* section heads in the editor */
.np-sec h3 { font-size: 16px; font-weight: 650; margin: 4px 0 2px; color: var(--ink); }
.np-sec p { font-size: 13.5px; color: var(--muted); margin: 0; }
.np-sec { margin-top: 6px; }

/* live */
.np-live { display: inline-block; vertical-align: 4px; margin-right: 10px; font: 600 13px/1 "Instrument Sans", sans-serif;
  font-stretch: 100%; letter-spacing: 0; color: #fff; background: var(--heat); padding: 5px 8px 5px 20px; border-radius: 5px; position: relative; }
.np-live::before { content: ""; position: absolute; left: 8px; top: 50%; width: 6px; height: 6px; margin-top: -3px; border-radius: 50%; background: #fff; }
.np-live.off { background: var(--sunk); color: var(--muted); border: 1px solid var(--rule); }
.np-live.off::before { background: var(--muted); }
.np-livemsg { margin: 12px 0 0; font-size: 14px; color: var(--muted); }
#np-stop { align-self: flex-end; margin-bottom: 14px; }

/* library + danger buttons */
.np-danger { background: var(--heat) !important; border-color: var(--heat) !important; color: #fff !important; }
.np-danger:hover { background: #C93422 !important; }
.np-danger-soft { color: #B5321F !important; }
.np-checks label { width: 100%; }
#np-sounds .wrap { display: flex; flex-direction: column; gap: 6px; max-height: 520px; overflow-y: auto; }
#np-sounds label { width: 100%; }
#np-msearch { min-height: 44px; align-self: flex-end; }

/* colour row */
#np-colours { align-items: center; gap: 6px; flex-wrap: wrap; }
#np-colours { flex-wrap: nowrap !important; }
#np-picker { flex: 0 0 auto !important; width: auto !important; padding: 0 !important; overflow: visible !important; background: transparent !important; }
#np-picker .dialog-button { display: block; width: 40px !important; height: 32px !important; border: 1px solid #B9BFC9 !important; border-radius: 6px !important; }
#np-minfo { padding: 2px 0 6px; }
.np-sw { min-width: 0 !important; font-weight: 500 !important; padding-left: 8px !important; }
.np-sw::before { content: ""; width: 14px; height: 14px; border-radius: 50%; display: inline-block; margin-right: 6px; border: 1px solid rgba(0,0,0,.25); vertical-align: -2px; }
#sw-white::before { background: #FFFFFF; } #sw-yellow::before { background: #FFD400; } #sw-sky::before { background: #3FA0FF; }
#sw-green::before { background: #39FF6A; } #sw-pink::before { background: #FF4FD8; }

/* media */
.np-media img, .np-media video { border-radius: 6px; }
#np-pv img { background: #000; }
"""


def section(title, note=""):
    return gr.HTML(f'<div class="np-sec"><h3>{esc(title)}</h3>' + (f"<p>{esc(note)}</p>" if note else "") + "</div>",
                   js_on_load=None)


CLICK_JS = ("element.addEventListener('click', (e) => { const b = e.target.closest('[data-clip]'); "
            "if (b) trigger('click', {clip: Number(b.dataset.clip)}); });")


def build():
    jobs0 = job_choices()
    with gr.Blocks(title="Clipper") as demo:
        with gr.Row(elem_id="np-mast", equal_height=False):
            with gr.Column(scale=4, min_width=280):
                gr.HTML('<div id="np-brand"><h1>Clipper</h1><p>Finds where chat went off in a stream, past or '
                        'live, and cuts those moments into vertical Shorts.</p></div>', js_on_load=None)
            with gr.Column(scale=7, min_width=360):
                with gr.Row(elem_id="np-find", equal_height=True):
                    url = gr.Textbox(label="Past stream link", show_label=False, container=False, elem_id="np-url",
                                     scale=5, lines=1, max_lines=1,
                                     placeholder="Paste a stream link: a past VOD or a channel that's live now")
                    go = gr.Button("Find clips", variant="primary", scale=1, min_width=130)
                with gr.Accordion("More options", open=False, elem_id="np-opts"):
                    with gr.Row():
                        top_n = gr.Slider(1, 30, value=10, step=1, label="How many clips to keep")
                        cookies = gr.Dropdown([("None", ""), ("Chrome", "chrome"), ("Safari", "safari"),
                                               ("Firefox", "firefox"), ("Brave", "brave"), ("Edge", "edge")], value="",
                                              label="Sign in with browser cookies (members-only or age-gated)")
                    with gr.Row():
                        hours = gr.Slider(0.5, 12, value=6, step=0.5, label="Live streams: stop watching after (hours)")
                        chat_file = gr.File(label="Chat file (exported Twitch or Kick chat, JSON or CSV)",
                                            file_types=[".json", ".csv"], height=110)

        with gr.Group(elem_id="np-streambox"):
            with gr.Row(elem_id="np-bar"):
                job = gr.Dropdown(choices=jobs0, value=jobs0[0][1] if jobs0 else None, label="Stream",
                                  interactive=True, scale=3)
                clip = gr.Dropdown(choices=[], label="Open clip", interactive=True, scale=2)
                stop_btn = gr.Button("Stop watching", visible=False, scale=0, min_width=150, elem_id="np-stop")
        with gr.Column(elem_id="np-chartbox", variant="panel"):
            status = gr.HTML(status_html(None), js_on_load=None)
            chart = gr.HTML(chart_html(None), js_on_load=CLICK_JS)

        with gr.Tabs():
            with gr.Tab("Clips"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=6, min_width=420):
                        clip_list = gr.HTML(list_html(None), js_on_load=CLICK_JS)
                    with gr.Column(scale=5, min_width=380):
                        card = gr.HTML(card_html(None, None), js_on_load=None)
                        src_vid = gr.Video(label="Clip as downloaded", show_label=False, interactive=False,
                                           elem_classes="np-media", visible=False)

            with gr.Tab("Make the Short"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=3, min_width=420):
                        section("Crops", "Pick a moment, then frame the face cam (blue) and the gameplay (orange).")
                        t = gr.Slider(0, 60, value=1, step=0.1, label="Frame to set the crops on (seconds)")
                        crop_view = gr.Image(show_label=False, interactive=False, elem_classes="np-media",
                                             buttons=[])
                        with gr.Row():
                            with gr.Column(min_width=200):
                                section("Face cam")
                                with gr.Group():
                                    fx = gr.Slider(0, 1, step=0.005, label="Left edge")
                                    fy = gr.Slider(0, 1, step=0.005, label="Top edge")
                                    fw = gr.Slider(0.03, 1, step=0.005, label="Width (height follows the band)")
                            with gr.Column(min_width=200):
                                section("Gameplay")
                                with gr.Group():
                                    gx = gr.Slider(0, 1, step=0.005, label="Left edge")
                                    gy = gr.Slider(0, 1, step=0.005, label="Top edge")
                                    gw = gr.Slider(0.05, 1, step=0.005, label="Width (height follows the band)")
                    with gr.Column(scale=2, min_width=360):
                        section("Preview", "The Short at this frame, 9:16.")
                        pv = gr.Image(show_label=False, interactive=False, height=PV_H, elem_id="np-pv",
                                      elem_classes="np-media", buttons=[])
                        section("Layout")
                        layout = gr.Radio([("Face on top", "faceTop"), ("Face at bottom", "faceBottom"), ("No face", "noFace")],
                                          show_label=False, value="faceTop")
                        split = gr.Slider(0.25, 0.55, step=0.01, value=0.4, label="Face band height (share of the frame)")
                        section("Opening")
                        with gr.Group():
                            hook_on = gr.Checkbox(label="Open with the hot moment, then play the full clip", value=True)
                            with gr.Row():
                                hs = gr.Number(label="Hook starts (s)", step=0.1)
                                he = gr.Number(label="Hook ends (s)", step=0.1)
                            with gr.Row():
                                ts = gr.Number(label="Clip starts (s)", step=0.1)
                                te = gr.Number(label="Clip ends (s)", step=0.1)
                        section("Subtitles", "One word at a time, no box, black shadow.")
                        with gr.Group():
                            with gr.Row():
                                subs_on = gr.Checkbox(label="Show subtitles", value=True)
                                upper = gr.Checkbox(label="Uppercase", value=True)
                        swatches = []
                        with gr.Row(elem_id="np-colours", equal_height=True):
                            color = gr.ColorPicker(label="Colour", show_label=False, container=False, value="#FFFFFF",
                                                   scale=0, min_width=48, elem_id="np-picker")
                            for name, hx in (("White", "#FFFFFF"), ("Yellow", "#FFD400"), ("Sky", "#3FA0FF"),
                                             ("Green", "#39FF6A"), ("Pink", "#FF4FD8")):
                                swatches.append((gr.Button(name, size="sm", min_width=40, elem_id=f"sw-{name.lower()}",
                                                           elem_classes="np-sw"), hx))
                        with gr.Group():
                            size = gr.Slider(50, 200, step=2, value=110, label="Size (px)")
                            sy = gr.Slider(0.08, 0.92, step=0.01, value=0.4, label="Height on screen (0 is the top)")
                        seam = gr.Button("Put words on the seam between face and gameplay", size="sm")
                        section("Export")
                        with gr.Row():
                            render_btn = gr.Button("Render Short", variant="primary", scale=2)
                            studio_btn = gr.Button("Open in Remotion Studio", scale=2)
                        render_msg = gr.HTML(js_on_load=None)
                        final_vid = gr.Video(label="Finished Short", show_label=False, interactive=False,
                                             elem_classes="np-media", visible=False)
                        files = gr.File(label="Downloads (MP4 and SRT)", file_count="multiple", interactive=False,
                                        visible=False)

            with gr.Tab("Memes"):
                with gr.Row(equal_height=False):
                    with gr.Column(scale=3, min_width=420):
                        section("Find a meme", "Video memes with sound are short clips (40 s or less) found on YouTube. "
                                "Meme sounds come from myinstants.com. Classic images come from memegen.link and Imgflip.")
                        m_src = gr.Radio([("Video memes with sound", "videos"), ("Meme sounds", "sounds"),
                                          ("Classic images", "classic")], value="videos", label="Source")
                        with gr.Row(equal_height=True):
                            m_q = gr.Textbox(label="Search (leave empty for popular ones)", scale=4,
                                             placeholder="bruh, emotional damage, vine boom")
                            m_search = gr.Button("Search memes", variant="primary", scale=1, min_width=150,
                                                 elem_id="np-msearch")
                        m_rows = gr.State([])
                        m_item = gr.State(None)
                        m_results = gr.Gallery(label="Results", show_label=False, columns=4, height=520,
                                               object_fit="cover", allow_preview=False)
                        m_sounds = gr.Radio([], label="Sounds: click one to hear it", visible=False,
                                            elem_id="np-sounds")
                    with gr.Column(scale=2, min_width=360):
                        section("Picked meme")
                        m_info = gr.Markdown("Click a result to pick it.", elem_id="np-minfo")
                        with gr.Group(visible=False) as m_text:
                            m_top = gr.Textbox(label="Top text (optional)")
                            m_bottom = gr.Textbox(label="Bottom text (optional)")
                        m_pv = gr.Image(label="Preview", show_label=False, interactive=False, height=320, buttons=[],
                                        elem_classes="np-media", visible=False)
                        m_pv_vid = gr.Video(label="Preview", show_label=False, interactive=False, height=320,
                                            elem_classes="np-media", visible=False)
                        m_pv_aud = gr.Audio(label="Preview", show_label=False, interactive=False, visible=False)
                        m_save = gr.Button("Save to this clip", variant="primary")
                        m_msg = gr.Markdown()
                        section("Saved for this clip", "Newest first. Sounds are in the downloads below.")
                        m_gallery = gr.Gallery(show_label=False, columns=3, height=240, object_fit="contain")
                        m_files = gr.File(label="Downloads", file_count="multiple", interactive=False, visible=False)

            with gr.Tab("Library") as lib_tab:
                with gr.Row(equal_height=False):
                    with gr.Column(min_width=420):
                        section("Streams", "Tick the streams to delete. Each goes with all its clips and memes.")
                        lib_streams = gr.CheckboxGroup([], show_label=False, elem_classes="np-checks")
                        with gr.Row():
                            lib_all_s = gr.Button("Select all", size="sm")
                            lib_del_s = gr.Button("Delete selected streams", size="sm", elem_classes="np-danger-soft")
                        with gr.Group(visible=False) as lib_conf_s:
                            lib_conf_s_msg = gr.HTML(js_on_load=None)
                            with gr.Row():
                                lib_yes_s = gr.Button("Delete", elem_classes="np-danger")
                                lib_no_s = gr.Button("Cancel")
                    with gr.Column(min_width=420):
                        section("Clips in the open stream", "Tick the clips to delete. The stream itself stays.")
                        lib_clips = gr.CheckboxGroup([], show_label=False, elem_classes="np-checks")
                        with gr.Row():
                            lib_all_c = gr.Button("Select all", size="sm")
                            lib_del_c = gr.Button("Delete selected clips", size="sm", elem_classes="np-danger-soft")
                        with gr.Group(visible=False) as lib_conf_c:
                            lib_conf_c_msg = gr.HTML(js_on_load=None)
                            with gr.Row():
                                lib_yes_c = gr.Button("Delete", elem_classes="np-danger")
                                lib_no_c = gr.Button("Cancel")
                lib_msg = gr.HTML(js_on_load=None)

        timer = gr.Timer(2.0)
        edit_inputs = [job, clip, t, layout, split, fx, fy, fw, gx, gy, gw, hook_on, hs, he, ts, te, subs_on, color, size, sy, upper]
        editor_fields = [t, layout, split, fx, fy, fw, gx, gy, gw, hook_on, hs, he, ts, te, subs_on, color, size, sy, upper]
        quiet = dict(show_progress="hidden")

        bar_out = [status, chart, clip_list, clip, stop_btn]
        go.click(start_analysis, [url, top_n, cookies, chat_file, hours], job)
        job.input(switch_job, job, bar_out, **quiet)
        job.change(lambda k, n: refresh_job(k, n), [job, clip], bar_out, **quiet)
        demo.load(first_load, job, bar_out, **quiet)
        timer.tick(tick, [job, clip], bar_out, **quiet)
        stop_btn.click(stop_live, job, stop_btn, **quiet)
        clip_list.click(pick_clip, None, clip, **quiet)
        chart.click(pick_clip, None, clip, **quiet)
        clip.change(highlight, [job, clip], [chart, clip_list], **quiet)
        clip.change(load_clip, [job, clip], [card, src_vid] + editor_fields + [final_vid, files], **quiet).then(
            apply_edit, edit_inputs, [crop_view, pv], **quiet)
        for comp in editor_fields:      # user edits only (not the values load_clip fills in)
            ev = comp.release if isinstance(comp, gr.Slider) else comp.input
            ev(apply_edit, edit_inputs, [crop_view, pv], **quiet)
        for btn, hx in swatches:
            btn.click(lambda h=hx: h, None, color, **quiet).then(apply_edit, edit_inputs, [crop_view, pv], **quiet)
        seam.click(snap_seam, [layout, split], sy, **quiet).then(apply_edit, edit_inputs, [crop_view, pv], **quiet)
        render_btn.click(apply_edit, edit_inputs, [crop_view, pv], **quiet).then(
            do_render, [job, clip], [render_msg, final_vid, files], **quiet)
        studio_btn.click(apply_edit, edit_inputs, [crop_view, pv], **quiet).then(open_studio, [job, clip], render_msg, **quiet)

        clip.change(meme_load, [job, clip], [m_q, m_gallery, m_files], **quiet)
        pv_out = [m_pv, m_pv_vid, m_pv_aud]
        for ev in (m_search.click, m_q.submit, m_src.change):
            ev(meme_search, [m_q, m_src], [m_results, m_sounds, m_rows, m_item, m_info, m_text] + pv_out)
        m_results.select(meme_pick, m_rows, [m_item, m_info, m_text], **quiet).then(
            meme_preview, [m_item, m_top, m_bottom], pv_out)
        m_sounds.input(meme_pick_sound, [m_rows, m_sounds], [m_item, m_info, m_text], **quiet).then(
            meme_preview, [m_item, m_top, m_bottom], pv_out)
        for box in (m_top, m_bottom):
            box.submit(meme_preview, [m_item, m_top, m_bottom], pv_out)
            box.blur(meme_preview, [m_item, m_top, m_bottom], pv_out)
        m_save.click(meme_save, [job, clip, m_item, m_top, m_bottom], [m_msg, m_gallery, m_files])

        lib_out = [lib_streams, lib_clips, lib_conf_s, lib_conf_c]
        lib_tab.select(lib_refresh, job, lib_out, **quiet)
        job.change(lib_refresh, job, lib_out, **quiet)
        lib_all_s.click(lambda: gr.update(value=[k for _, k in lib_stream_choices()]), None, lib_streams, **quiet)
        lib_all_c.click(lambda k: gr.update(value=[i for _, i in lib_clip_choices(k)]), job, lib_clips, **quiet)
        lib_del_s.click(ask_streams, lib_streams, [lib_conf_s_msg, lib_conf_s], **quiet)
        lib_del_c.click(ask_clips, [job, lib_clips], [lib_conf_c_msg, lib_conf_c], **quiet)
        lib_no_s.click(lambda: gr.update(visible=False), None, lib_conf_s, **quiet)
        lib_no_c.click(lambda: gr.update(visible=False), None, lib_conf_c, **quiet)
        lib_yes_s.click(do_delete_streams, [lib_streams, job], [job, clip, lib_msg] + lib_out, **quiet)
        lib_yes_c.click(do_delete_clips, [job, lib_clips, clip], [clip, clip_list, chart, lib_msg] + lib_out, **quiet)
    return demo


def serve(port=8765):
    build().launch(server_name="127.0.0.1", server_port=port, allowed_paths=[str(JOBS)], inbrowser=True,
                   css=CSS, theme=THEME, head=HEAD, js=FORCE_LIGHT)
