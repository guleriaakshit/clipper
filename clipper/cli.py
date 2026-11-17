"""clipper analyze <url> [--top 10] [--by chat,audio,speech] [--words "no way,let's go"] [--min-score 85] [--cookies-from-browser chrome]
                [--chat-file f.json] [--no-download]
clipper live <twitch channel or live YouTube link> [--hours 6] [--by chat,audio,speech] [--words ...]
clipper stop <job-key>
clipper serve [--port 8765]
clipper render <job-key> <clip-n>
clipper list
clipper stt-download
clipper update                                  pull the latest Clipper from GitHub
clipper brief <job-key> <clip-n> [--open]       write the clip's hand-off for Claude (--open: start Claude Code on it)
clipper meme search <words> [--kind videos|sounds|images]
clipper meme get <job-key> <clip-n> <meme-id> [--top TEXT] [--bottom TEXT]"""
import argparse
import json

from .common import JOBS, require_home, read_json


def main():
    require_home()
    ap = argparse.ArgumentParser(prog="clipper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("url")
    a.add_argument("--top", type=int, default=10)
    a.add_argument("--cookies-from-browser", default=None)
    a.add_argument("--chat-file", default=None)
    a.add_argument("--no-download", action="store_true")
    a.add_argument("--by", default="chat", help="comma list of chat, audio, speech (default chat)")
    a.add_argument("--words", default="", help="words to listen for, comma separated (with --by speech)")
    a.add_argument("--min-score", type=int, default=85, help="only keep clips scoring at least this (0-100, default 85)")
    a.add_argument("--chat-x", type=float, default=2.0, help="chat spike = at least this many times its average rate (default 2)")
    a.add_argument("--loud-x", type=float, default=2.0,
                   help="loud voice = at least this many times the streamer's usual level (default 2 = +6 dB)")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8765)
    r = sub.add_parser("render")
    r.add_argument("key")
    r.add_argument("n", type=int)
    sub.add_parser("list")
    lv = sub.add_parser("live")
    lv.add_argument("url")
    lv.add_argument("--hours", type=float, default=6.0)
    lv.add_argument("--by", default="chat")
    lv.add_argument("--words", default="")
    lv.add_argument("--min-score", type=int, default=85)
    lv.add_argument("--chat-x", type=float, default=2.0)
    lv.add_argument("--loud-x", type=float, default=2.0)
    lr = sub.add_parser("live-run")
    lr.add_argument("key")
    st = sub.add_parser("stop")
    st.add_argument("key")
    sub.add_parser("stt-download", help="download Parakeet v2 (live streams, and past streams without a GPU)")
    sub.add_parser("stt-hq", help="set up Qwen3-ASR on the NVIDIA GPU for past streams (~9 GB)")
    sub.add_parser("update")
    b = sub.add_parser("brief")
    b.add_argument("key")
    b.add_argument("n", type=int)
    b.add_argument("--open", action="store_true", help="also open Claude Code on it in a new terminal")
    m = sub.add_parser("meme")
    msub = m.add_subparsers(dest="meme_cmd", required=True)
    ms = msub.add_parser("search")
    ms.add_argument("words", nargs="*")
    ms.add_argument("--kind", choices=["videos", "sounds", "images"], default="videos")
    ms.add_argument("--limit", type=int, default=15)
    mg = msub.add_parser("get")
    mg.add_argument("key")
    mg.add_argument("n", type=int)
    mg.add_argument("id", help="an id from `clipper meme search`, e.g. yt:abc123 or myinstants:vine-boom.mp3")
    mg.add_argument("--name", default="")
    mg.add_argument("--top", default="")
    mg.add_argument("--bottom", default="")
    args = ap.parse_args()

    if args.cmd == "analyze":
        from .pipeline import analyze
        jd = analyze(args.url, top_n=args.top, cookies_browser=args.cookies_from_browser,
                     chat_file=args.chat_file, skip_download=args.no_download,
                     basis=args.by.split(","), keywords=args.words, min_score=args.min_score, chat_x=args.chat_x,
                     loud_x=args.loud_x)
        c = read_json(jd / "clips.json")
        print(f"job: {jd.name}  messages: {c['messages']}  average: {c['avg_per_min']}/min  minimum score: {c.get('min_score')}")
        if not c["clips"]:
            print(read_json(jd / "status.json", {}).get("msg", ""))
        for x in c["clips"]:
            print(f"  #{x['id']}  score {x['score']:>3}  {'+'.join(x.get('basis', ['chat']))}  at {x['vod_time']}  {x['auto_reason'][:90]}")
    elif args.cmd == "serve":
        from .app import serve
        print(f"Clipper dashboard: http://localhost:{args.port}")
        serve(args.port)
    elif args.cmd == "render":
        from .render import render
        print(render(JOBS / args.key, args.n, lambda p, m: print(f"\r{m} {p:.0f}%", end="", flush=True)))
    elif args.cmd == "live":
        from . import live
        target = live.probe(args.url)
        if not target:
            raise SystemExit("That link isn't live right now. For a past stream use: clipper analyze <url>")
        jd = live.new_job(target, hours=args.hours, basis=args.by.split(","), keywords=args.words,
                          min_score=args.min_score, chat_x=args.chat_x, loud_x=args.loud_x)
        print(f"Watching {target['channel']} live. Job: {jd.name}. Ctrl+C or `clipper stop {jd.name}` to stop.")
        try:
            live.run(jd.name)
        except KeyboardInterrupt:
            pass
        print(read_json(jd / "status.json", {}).get("msg", ""))
    elif args.cmd == "live-run":
        from . import live
        live.run(args.key)
    elif args.cmd == "stop":
        from . import live
        live.stop(JOBS / args.key)
        print("Stopping", args.key)
    elif args.cmd == "stt-download":
        from . import stt
        if stt.available() and stt.model_dir().name == stt.NAME:
            print("Speech-to-text model already installed at", stt.model_dir())
        else:
            print("Installed at", stt.download())
    elif args.cmd == "stt-hq":
        from . import stt
        try:
            stt.hq_install()
        except Exception as e:
            raise SystemExit(f"Couldn't set up the GPU speech-to-text: {e}")
    elif args.cmd == "update":
        from .update import update
        update()
    elif args.cmd == "brief":
        from . import handoff
        print(handoff.brief(args.key, args.n))
        if args.open and not handoff.open_claude(args.key, args.n):
            print("Couldn't open Claude Code here. Start it in the Clipper folder and type:", handoff.prompt(args.key, args.n))
    elif args.cmd == "meme":
        from . import memes
        try:
            if args.meme_cmd == "search":
                for r in memes.search(" ".join(args.words), args.kind)[:args.limit]:
                    extra = f"  {r['duration']:.0f} s" if r.get("duration") else ""
                    print(f"{r['id']}	{r['name']}{extra}")
            else:
                cd = JOBS / args.key / "clips" / str(args.n)
                if not cd.is_dir():
                    raise SystemExit(f"No clip {args.n} in {args.key}.")
                print(memes.save(cd, memes.by_id(args.id, args.name), args.top, args.bottom))
        except memes.MemeError as ex:
            raise SystemExit(str(ex))
    elif args.cmd == "list":
        for jd in sorted(JOBS.iterdir()):
            c = read_json(jd / "clips.json", {}) or {}
            print(jd.name, "|", c.get("title"), "|", len(c.get("clips", [])), "clips |",
                  json.dumps(read_json(jd / "status.json", {})))


if __name__ == "__main__":
    main()
