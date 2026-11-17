"""clipper analyze <url> [--top 10] [--by chat,audio,speech] [--words "no way,let's go"] [--min-score 85] [--cookies-from-browser chrome]
                [--chat-file f.json] [--no-download]
clipper live <twitch channel or live YouTube link> [--hours 6] [--by chat,audio,speech] [--words ...]
clipper stop <job-key>
clipper serve [--port 8765]
clipper render <job-key> <clip-n>
clipper list
clipper stt-download"""
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
    lr = sub.add_parser("live-run")
    lr.add_argument("key")
    st = sub.add_parser("stop")
    st.add_argument("key")
    sub.add_parser("stt-download")
    args = ap.parse_args()

    if args.cmd == "analyze":
        from .pipeline import analyze
        jd = analyze(args.url, top_n=args.top, cookies_browser=args.cookies_from_browser,
                     chat_file=args.chat_file, skip_download=args.no_download,
                     basis=args.by.split(","), keywords=args.words, min_score=args.min_score)
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
                          min_score=args.min_score)
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
        if stt.available():
            print("Speech-to-text model already installed at", stt.model_dir())
        else:
            print("Installed at", stt.download())
    elif args.cmd == "list":
        for jd in sorted(JOBS.iterdir()):
            c = read_json(jd / "clips.json", {}) or {}
            print(jd.name, "|", c.get("title"), "|", len(c.get("clips", [])), "clips |",
                  json.dumps(read_json(jd / "status.json", {})))


if __name__ == "__main__":
    main()
