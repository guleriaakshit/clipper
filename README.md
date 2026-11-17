# Clipper

Clipper finds the moments in a stream where chat lost it, cuts them out in the best quality available, and turns them into vertical Shorts.

It works on **past streams** (Twitch VODs, YouTube streams, Kick VODs) and on **streams that are live right now** (Twitch channels, live YouTube streams). A chat spike is a stretch where chat runs at 2× the stream's average or more. Each spike gets a score out of 100. The score weighs how far chat jumped, how many different people were typing, how long the spike lasted and what kind of reaction it was (laughing, hype, shock, "clip it").

The dashboard runs on your own computer:

- a chart of chat activity across the stream, with each clip marked;
- a ranked list of clips with what chat reacted to;
- a Short editor: crop the face cam and the gameplay, put the face on top or at the bottom, open on the hottest moment, and add one-word subtitles in the colour you pick;
- memes for each clip: short video memes with sound, meme sounds, and classic meme images with your own top and bottom text;
- a library to delete streams and clips you don't need any more (they go to the Trash).

## Install

macOS or Linux (on Windows, use WSL):

```sh
curl -fsSL https://raw.githubusercontent.com/guleriaakshit/clipper/main/install.sh | bash
```

The installer checks for git, ffmpeg and Python 3.11+ and offers to install anything missing. It sets up Clipper in `~/Clipper` (you can pick another folder). Then it asks:

1. **Speech-to-text for subtitles?** It downloads NVIDIA Parakeet (~490 MB), which runs offline. If you skip it, Shorts have no subtitles and everything else works.
2. **The Short renderer?** It installs Remotion, which needs Node.js 18+ (~400 MB).
3. **A `clipper` terminal command?**
4. **The `/clip` skill for Claude Code?** Asked only if you use Claude Code.

To install without questions, add `--yes` (`... | bash -s -- --yes`). To update, run the same command again.

## Use

```sh
clipper serve                                   # dashboard at http://localhost:8765
clipper analyze https://www.twitch.tv/videos/…  # a past stream
clipper live https://www.twitch.tv/somechannel  # a stream that's live now (Ctrl+C or `clipper stop <job>` to stop)
clipper render <job> <clip number>              # render a Short from the terminal
clipper list                                    # every stream you've clipped
clipper stt-download                            # add speech-to-text later
```

You can also paste any of these links straight into the dashboard.

### Past streams

- **Twitch:** chat comes from Twitch's public web API. No login is needed.
- **YouTube:** chat comes from the video's chat replay, which must have been on during the stream.
- **Kick:** Kick's chat history sits behind Cloudflare's bot check, so Clipper doesn't read it. Export the chat with another tool and add it with `--chat-file chat.json` (or under *More options* in the dashboard). This accepts TwitchDownloader JSON, chat-downloader JSON, or a CSV with `t,user,text` columns.
- **Members-only or age-gated videos:** use `--cookies-from-browser chrome` (or safari, firefox, brave, edge).

Clipper downloads only the clips themselves, in the best resolution and then the highest frame rate available.

### Live streams

Clipper watches the stream as it happens:

- It reads chat live: Twitch through its public chat server, read-only and without a login; YouTube through yt-dlp.
- It records the video into a rolling 10-minute buffer.
- It cuts each spike into a clip once chat calms down.

Times count from when Clipper started watching. It stops when you press **Stop watching**, when the stream ends, or after the number of hours set under *More options* (6 by default). Kick live streams aren't supported, for the same Cloudflare reason as above.

### Making a Short

Open a clip, go to **Make the Short**, frame the face cam and the gameplay, and pick the layout and subtitle style. Then press **Render Short**. The Short opens with the clip's loudest moment, then plays the whole clip. You get `final.mp4` (1080×1920, at the source frame rate up to 60 fps) and `final.srt`.

## How it works

| Step | Code |
| --- | --- |
| Chat → messages per 5 s → average → spikes → score | `clipper/spikes.py` |
| Clip window: starts 20 s before chat reacts and ends after chat calms down (20–60 s) | `clipper/spikes.py` |
| Download only that part (`yt-dlp --download-sections`) | `clipper/download.py` |
| Live: chat readers, rolling recorder, cutting | `clipper/live.py` |
| Hottest moment: the loudest 3 s just before chat peaks | `clipper/hotspot.py` |
| Word timings (Parakeet through sherpa-onnx) | `clipper/stt.py`, `clipper/subs.py` |
| Face cam guess (OpenCV) | `clipper/face.py` |
| Render (Remotion) | `clipper/render.py`, `remotion/src/` |
| Dashboard (Gradio) | `clipper/app.py` |

Everything Clipper downloads or makes stays in the install folder: `jobs/` for streams and clips, `tmp/` for caches.

## Notes

- Clipper works with other people's streams and memes. Check what you're allowed to post where you post it.
- Meme sources: YouTube for short video memes (fetched with yt-dlp), [myinstants.com](https://www.myinstants.com) for sounds, and [memegen.link](https://memegen.link) and [Imgflip](https://imgflip.com) for images.

MIT licence.
