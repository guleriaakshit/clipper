# Clipper

Clipper finds the moments in a stream where chat lost it, cuts them out in the best quality available, and turns them into vertical Shorts.

It works on **Twitch, YouTube and Kick**, both on **past streams** (VODs) and on **streams that are live right now**.

You choose how clips are found, one way or several at once:

- **Chat spikes:** chat runs at 2× the stream's average or more. The score weighs how far chat jumped, how many different people were typing, how long the spike lasted and what kind of reaction it was (laughing, hype, shock, "clip it").
- **What's said:** reaction phrases ("no way", "oh my god", "let's go"…) and any words you give it. Past streams use YouTube's own auto-generated subtitles, so nothing is listened to or transcribed. Twitch and Kick VODs have no subtitles, so this option isn't available for them. Live streams are transcribed on your computer (needs the speech-to-text model).
- **Streamer gets loud** (live streams only): the streamer's voice goes at least 6 dB above their usual level over the last 10 minutes.

Each moment gets a score out of 100, and moments where several signals agree rank higher. Only clips at or above the **minimum score** (85 by default) are kept, so you don't get weak ones; if nothing makes it, Clipper tells you the best score it found. Every clip shows which combination of signals found it (for example *Chat spikes + What's said*).

The dashboard runs on your own computer:

- a library of your streams on the left: open one, or tick several and delete them;
- a chart of chat activity (and the streamer's voice, if you picked it) across the stream, with each clip marked;
- a ranked list of clips with what chat reacted to and what the streamer said; tick clips to delete several at once;
- a Short editor: crop the face cam and the gameplay, put the face on top or at the bottom, open on the hottest moment, and add one-word subtitles in the colour you pick;
- memes for each clip: short video memes with sound, meme sounds, and classic meme images with your own top and bottom text;
- every delete asks first, and deleted files go to the Trash.

## Install

macOS or Linux (on Windows, use WSL):

```sh
curl -fsSL https://raw.githubusercontent.com/guleriaakshit/clipper/main/install.sh | bash
```

The installer checks for git, ffmpeg and Python 3.11+ and offers to install anything missing. It sets up Clipper in `~/Clipper` (you can pick another folder). Then it asks:

1. **Speech-to-text?** It downloads NVIDIA Parakeet (~490 MB), which runs offline. It's used for live streams and for the subtitles of Twitch and Kick clips (YouTube clips use YouTube's own subtitles). If you skip it, those get no subtitles and everything else works.
2. **The Short renderer?** It installs Remotion, which needs Node.js 18+ (~400 MB).
3. **A `clipper` terminal command?**
4. **The `/clip` skill for Claude Code?** Asked only if you use Claude Code.

To install without questions, add `--yes` (`... | bash -s -- --yes`). To update, run the same command again.

## Use

```sh
clipper serve                                   # dashboard at http://localhost:8765
clipper analyze https://www.twitch.tv/videos/…  # a past stream, found by chat spikes
clipper analyze <link> --by chat,speech --words "no way,clip that" --min-score 80   # chat + what's said, keep 80+
clipper live https://kick.com/somechannel      # a stream that's live now (Twitch, YouTube or Kick; `clipper stop <job>` to stop)
clipper render <job> <clip number>              # render a Short from the terminal
clipper list                                    # every stream you've clipped
clipper stt-download                            # add speech-to-text later
```

You can also paste any of these links straight into the dashboard.

### Past streams

- **Twitch:** chat comes from Twitch's public web API. No login is needed.
- **YouTube:** chat comes from the video's chat replay, which must have been on during the stream.
- **Kick:** chat comes from Kick's chat replay, the same public endpoint kick.com uses for logged-out visitors. A long VOD is read in parallel ten-minute parts: a 9.6-hour stream with 27,000 messages took about 6 minutes. If Kick ever asks for a bot check, Clipper stops instead of trying to get around it.
- **Any platform:** you can also add an exported chat file with `--chat-file chat.json` (or under *More options* in the dashboard). This accepts TwitchDownloader JSON, chat-downloader JSON, or a CSV with `t,user,text` columns.
- **Members-only or age-gated videos:** use `--cookies-from-browser chrome` (or safari, firefox, brave, edge).

Clipper downloads only the clips themselves, in the best resolution and then the highest frame rate available.

### Live streams

Clipper watches the stream as it happens:

- It reads chat live: Twitch through its public chat server, read-only and without a login; YouTube through yt-dlp; Kick through Kick's realtime chat service with an anonymous token.
- It records the video into a rolling 10-minute buffer.
- It cuts each spike into a clip once chat calms down.

Times count from when Clipper started watching. It stops when you press **Stop watching**, when the stream ends, or after the number of hours set under *More options* (6 by default).

### Making a Short

Open a clip, go to **Make the Short**, frame the face cam and the gameplay, and pick the layout and subtitle style. Then press **Render Short**. The Short opens with the clip's loudest moment, then plays the whole clip. You get `final.mp4` (1080×1920, at the source frame rate up to 60 fps) and `final.srt`.

## How it works

| Step | Code |
| --- | --- |
| Chat → messages per 5 s → average → spikes → score | `clipper/spikes.py` |
| Loud voice and what's said, mixed with chat | `clipper/signals.py` |
| YouTube's own subtitles for past streams | `clipper/captions.py` |
| Clip window: starts 20 s before chat reacts and ends after chat calms down (20–60 s) | `clipper/spikes.py` |
| Download only that part (`yt-dlp --download-sections`) | `clipper/download.py` |
| Chat for each platform (Twitch, YouTube, Kick, chat files) | `clipper/chat/` |
| Live: chat readers, rolling recorder, cutting | `clipper/live.py` |
| Hottest moment: the loudest 3 s just before chat peaks | `clipper/hotspot.py` |
| Word timings (Parakeet through sherpa-onnx) | `clipper/stt.py`, `clipper/subs.py` |
| Face cam guess (OpenCV) | `clipper/face.py` |
| Render (Remotion) | `clipper/render.py`, `remotion/src/` |
| Dashboard (Gradio) | `clipper/app.py` |

Everything Clipper downloads or makes stays in the install folder: `jobs/` for streams and clips, `tmp/` for caches.

## Notes

- Clipper works with other people's streams and memes. Check what you're allowed to post where you post it.
- The dashboard font is Instrument Sans (SIL Open Font License, `clipper/assets/OFL.txt`).
- Meme sources: YouTube for short video memes (fetched with yt-dlp), [myinstants.com](https://www.myinstants.com) for sounds, and [memegen.link](https://memegen.link) and [Imgflip](https://imgflip.com) for images.

MIT licence.
