---
name: clip
description: Run Clipper on a stream (a past Twitch, YouTube or Kick VOD, or a Twitch/YouTube stream that's live now) to find chat spikes, cut those moments into clips, write what happens and why each could do well as a Short, and open the editor dashboard. Use when the user says /clip, gives a stream or VOD link to clip, or asks for clips from a stream.
---

# /clip <url> [top N]

Clipper is installed where the `clipper` command points (`readlink -f "$(command -v clipper)"`, its folder is the install folder; default `~/Clipper`). Run everything through `clipper`.

1. **Check it's installed**: `command -v clipper || echo missing`. If missing, tell the user to run the installer:
   `curl -fsSL https://raw.githubusercontent.com/guleriaakshit/clipper/main/install.sh | bash` and stop.
2. **Start the job** (in the background, it can take a while):
   - Past stream: `clipper analyze "<url>" --top <N, default 10>`. Ask what to clip on if the user hasn't said: `--by chat` (default), `speech` (what's said, from YouTube's own subtitles; Twitch/Kick VODs have none), `audio` (streamer gets loud, live streams only), or a mix like `--by chat,audio`; `--words "no way,clip that"` adds phrases to listen for.
     Kick VODs need an exported chat file (`--chat-file <path>`): Kick's chat API sits behind Cloudflare's bot check. Members-only or age-gated videos: add `--cookies-from-browser chrome` (ask which browser).
   - Live now (a `twitch.tv/<channel>` link or a live YouTube link): `clipper live "<url>" --hours <how long, default 6>`. Clips appear as chat spikes calm down; stop with `clipper stop <job>`.
   - Start the dashboard too: `clipper serve` (background) and give the user http://localhost:8765.
3. **Write what + why** once `jobs/<job>/clips.json` exists in the install folder. For each clip read `sample_chat`, `signals`, `top_words`, `multiple` and, if downloaded, `jobs/<job>/clips/<n>/words.json` (what the streamer said).
   - `what`: one plain sentence on what happens, built only from the chat and the transcript. If it's unclear, say so. Never invent events, names or game details.
   - `why`: 2–3 sentences on why it would hold a Shorts viewer: the spike size ("3.4× the stream's average chat"), the kind of reaction, and whether the hot moment works as a cold open. Plain statements, no hype words.
   Write both into `clips.json` with a small edit using the install folder's `.venv/bin/python`, keeping every other field.
4. **Report**: a table of clips (number, score, × average, time, what) and the dashboard link. Remind the user to pick face cam position, hook and subtitle colour in the Make the Short tab, then press Render Short.

The Short opens with the hot moment, then plays the full clip; subtitles are one word at a time with a black shadow. Don't render without the user's settings unless asked.
