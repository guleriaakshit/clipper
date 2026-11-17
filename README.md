# Clipper

Clipper finds the moments in a stream where chat lost it, cuts them out in the best quality available, and turns them into vertical Shorts.

It works on **Twitch, YouTube and Kick**, both on **past streams** (VODs) and on **streams that are live right now**.

You choose how clips are found, one way or several at once:

- **Chat spikes:** chat runs at 2× the stream's average or more. The score weighs how far chat jumped, how many different people were typing, how long the spike lasted and what kind of reaction it was (laughing, hype, shock, "clip it").
- **What's said:** reaction phrases ("no way", "oh my god", "let's go"…) and any words you give it. Clipper always looks for the stream's own subtitles first (YouTube's auto-generated ones, or uploaded subtitles on any platform). Only when there are none (Twitch and Kick VODs, some YouTube streams) does it download the audio and transcribe the whole stream on your computer. Live streams are transcribed as they play.
- **Streamer gets loud:** the streamer's voice goes at least 6 dB above their usual level over the last 10 minutes. For a past stream, Clipper downloads its audio first.

Listening to a past stream means downloading its audio (on Kick, which has no audio-only version, the smallest video: around a few hundred MB per hour) and, for what's said, transcribing it. The dashboard shows a progress bar for each step with the time left. The audio is deleted once it's read; the voice levels and the transcript are kept, so running the same stream again skips this.

Each moment gets a score out of 100, and moments where several signals agree rank higher. Only clips at or above the **minimum score** (85 by default) are kept, so you don't get weak ones; if nothing makes it, Clipper tells you the best score it found. Every clip shows which combination of signals found it (for example *Chat spikes + What's said*).

The dashboard runs on your own computer:

- a library of your streams on the left: open one, or tick several and delete them;
- a chart of chat activity (and the streamer's voice, if you picked it) across the stream, with each clip marked;
- a ranked list of clips with what chat reacted to and what the streamer said; tick clips to delete several at once;
- a Short editor: crop the face cam and the gameplay, put the face on top or at the bottom, open on the hottest moment, and add one-word subtitles in the colour you pick;
- **Send to Claude:** one button hands the raw clip to Claude Code, which edits it into a finished Short (subtitles, cold open, zooms, memes, censoring) and refines it with you;
- every delete asks first, and deleted files go to the Trash.

## Install

macOS or Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/guleriaakshit/clipper/main/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/guleriaakshit/clipper/main/install.ps1 | iex
```

The installer checks for git, ffmpeg and Python 3.11+ and offers to install anything missing (with Homebrew, apt, dnf or pacman on macOS and Linux, and winget on Windows). It sets up Clipper in `~/Clipper` (`%USERPROFILE%\Clipper` on Windows; you can pick another folder). Then it asks:

1. **Speech-to-text?** Only used when a stream has no subtitles of its own. It downloads NVIDIA Parakeet TDT 0.6B v2 (~480 MB, English), which runs offline on the CPU, fast enough for live streams. If you skip it, Twitch and Kick clips get no subtitles and everything else works.
2. **Better speech-to-text for past streams?** Asked only when there's an NVIDIA GPU. Past streams don't have to keep up in real time, so they can use Qwen3-ASR 1.7B with Qwen3-ForcedAligner for word timings: more accurate, with punctuation. It needs its own Python environment with PyTorch (`.venv-stt`, ~9 GB in all) and about 6 GB of free GPU memory, so close other GPU-heavy apps (DaVinci Resolve, games) while it runs. Without it, or if it fails, past streams use Parakeet.
3. **The Short renderer?** It installs Remotion, which needs Node.js 18+ (~400 MB).
4. **A `clipper` terminal command?**
5. **The `/clip` and `/short` skills for Claude Code?** Asked only if you use Claude Code.

To install without questions, add `--yes` (`... | bash -s -- --yes`; on Windows set `$env:CLIPPER_YES = "1"` first).

### Update

```sh
clipper update
```

This pulls the latest Clipper from GitHub and refreshes whatever changed: the Python packages, the renderer's packages, and the Claude Code skills. Your streams, clips and settings aren't touched. Restart `clipper serve` afterwards if it was running. (Installed before `clipper update` existed? Run the install command once more, then use `clipper update` from then on.)

## Use

```sh
clipper serve                                   # dashboard at http://localhost:8765
clipper analyze https://www.twitch.tv/videos/…  # a past stream, found by chat spikes
clipper analyze <link> --by chat,speech --words "no way,clip that" --min-score 80   # chat + what's said, keep 80+
clipper live https://kick.com/somechannel      # a stream that's live now (Twitch, YouTube or Kick; `clipper stop <job>` to stop)
clipper render <job> <clip number>              # render a Short from the terminal
clipper list                                    # every stream you've clipped
clipper stt-download                            # add speech-to-text later (Parakeet v2)
clipper stt-hq                                  # GPU model for past streams (NVIDIA, ~9 GB)
clipper update                                  # get the latest version from GitHub
clipper brief <job> <clip number> --open        # hand a clip to Claude Code (same as the Send to Claude button)
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

**With Claude (recommended):** open a clip and press **Send to Claude**. Clipper writes a brief for the clip (`clips/<n>/brief.md`: the files, the chat at the spike, what the streamer says with timings) and opens Claude Code in a new terminal with `/short <job> <clip>`. Claude looks at the clip, then edits it in the house style:

- the face cam stacked as a 30% band on top or bottom, no border;
- a cold open on the hottest moment, then the full clip;
- one word at a time subtitles, no box, black shadow, in the colour that suits the footage, with the key words emphasised;
- words that would cost ad revenue muted with a beep and shown as `F***`;
- punch-in zooms, a shake and flash on the big hit, a warm or cold grade, meme videos, images and sounds where they land a punchline;
- 10 hook lines to pick from.

It renders, checks the frames, tells you what it did, and changes what you ask for. If Claude Code isn't installed, the button shows the command to paste into it.

**By hand:** go to **Make the Short**, frame the face cam and the gameplay, and pick the layout and subtitle style. Then press **Render Short**.

Either way, the Short opens with the clip's loudest moment, then plays the whole clip. You get `final.mp4` (1080×1920, at the source frame rate up to 60 fps) and `final.srt`.

Effects live in `clips/<n>/fx.json` next to the dashboard's `edit.json`; the full list of fields is in `clipper/render.py` (`normalize_fx`). Memes for an edit come from `clipper meme search <words> [--kind videos|sounds|images]` and `clipper meme get <job> <clip> <id>`.

## How it works

| Step | Code |
| --- | --- |
| Chat → messages per 5 s → average → spikes → score | `clipper/spikes.py` |
| Loud voice and what's said, mixed with chat | `clipper/signals.py` |
| YouTube's own subtitles for past streams | `clipper/captions.py` |
| Listening to a past stream: audio download, voice levels, transcript | `clipper/listen.py` |
| Clip window: starts 20 s before chat reacts and ends after chat calms down (20–60 s) | `clipper/spikes.py` |
| Download only that part (`yt-dlp --download-sections`) | `clipper/download.py` |
| Chat for each platform (Twitch, YouTube, Kick, chat files) | `clipper/chat/` |
| Live: chat readers, rolling recorder, cutting | `clipper/live.py` |
| Hottest moment: the loudest 3 s just before chat peaks | `clipper/hotspot.py` |
| Word timings: the stream's own subtitles first, else Parakeet v2 (live, CPU) or Qwen3-ASR (past streams, GPU worker in `.venv-stt`) | `clipper/captions.py`, `clipper/stt.py`, `clipper/stt_worker.py`, `clipper/subs.py` |
| Face cam guess (OpenCV) | `clipper/face.py` |
| Render (Remotion), effects from `fx.json` | `clipper/render.py`, `remotion/src/` |
| Memes for edits (YouTube, myinstants, memegen/Imgflip) | `clipper/memes.py` |
| Hand-off to Claude (`brief.md`, opening Claude Code) | `clipper/handoff.py`, `skills/short/` |
| `clipper update` | `clipper/update.py` |
| Dashboard (Gradio) | `clipper/app.py` |

Everything Clipper downloads or makes stays in the install folder: `jobs/` for streams and clips, `tmp/` for caches.

## Notes

- Clipper works with other people's streams and memes. Check what you're allowed to post where you post it.
- The dashboard font is Instrument Sans (SIL Open Font License, `clipper/assets/OFL.txt`).
- Meme sources: YouTube for short video memes (fetched with yt-dlp), [myinstants.com](https://www.myinstants.com) for sounds, and [memegen.link](https://memegen.link) and [Imgflip](https://imgflip.com) for images.

MIT licence.
