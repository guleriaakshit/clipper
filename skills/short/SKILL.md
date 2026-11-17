---
name: short
description: Edit a raw Clipper clip into a finished vertical Short (1080x1920) with word-by-word subtitles with a black shadow, a cold open, punch-in zooms, shakes, a colour grade, memes, meme sounds, censoring of ad-unfriendly words, then render it and refine it with the user. Use when the user runs /short <job> <clip>, presses "Send to Claude" in the Clipper dashboard, or asks to edit, finish or add effects or memes to a Clipper clip.
---

# /short <job> <clip>

You turn one raw clip from Clipper into a Short people don't swipe away. Clipper found and cut the moment; you do the edit: framing, cold open, subtitles, effects, memes, censoring, then render and refine until the user is happy.

## 0. Find the clip

- Run everything through the `clipper` command (`clipper.cmd` on Windows). The dashboard's "Send to Claude" button starts you in the Clipper folder; otherwise `clipper brief <job> <clip>` prints the brief's full path, and the Clipper folder is the part before `jobs/`.
- If you weren't given a job and clip, run `clipper list` and ask which one.
- Read `jobs/<job>/clips/<clip>/brief.md` (`clipper brief <job> <clip>` rewrites it). It lists the files, the moment (score, chat, what happens), the transcript with times, and how source times map to times in the Short.
- Files in the clip folder: `source.mp4` (the raw clip), `words.json` (word timings in source seconds), `edit.json` (framing, hook, subtitle style), `fx.json` (your effects), `memes/`, and `final.mp4` once rendered.

## 1. Watch it before deciding anything

You can't play video, so look at frames and read what's said:
- Frames: `ffmpeg -v error -ss <t> -i source.mp4 -frames:v 1 -vf scale=640:-1 <scratch>/f_<t>.jpg` every 2-3 s, plus 0.5 s steps around the hottest moment (`clip.json` → `hook`). Open them with Read.
- Read the transcript and the chat in the brief. Work out: the setup, the payoff (the moment chat exploded), and the reaction.
- Find the face cam in a frame and the gameplay area. Check that `edit.json` → `faceRect`/`gameRect` frame them (normalised `[x, y, w, h]` of the source). Fix them if they're off.
- Never invent events, names or game details. If the frames and transcript don't make it clear, say so and ask.

## 2. House style (the user's rules: always apply these)

1. **Face cam stacked, 30% of the frame.** The camera is a band covering the top (or bottom) 30% of the Short, edge to edge. No border, frame, rounded corners or inset. `edit.json`: `"layout": "faceTop"` (or `"faceBottom"` if the user prefers), `"split": 0.3`. Gameplay fills the rest. No face cam in the stream: `"layout": "noFace"`.
2. **Cold open, then the full clip.** Open on the hottest moment (1-3 s: `hookStart`/`hookEnd`, `hookEnabled: true`), then play the **whole** clip from start to end: `trimStart: 0`, `trimEnd`: the clip's full length. Don't shorten the body unless the user asks.
3. **Subtitles: one word at a time, no box, just text with a black shadow.** Pick the colour that reads best on this footage (`subColor`: white by default; yellow `#FFD400`, green `#39FF6A` or sky `#3FA0FF` when the gameplay is bright or white-heavy). Uppercase. Put them on the seam between face and gameplay: `subY` = `split` for faceTop, `1 - split` for faceBottom. Use `emphasis` (below) for the 2-5 key words.
4. **Censor what would cost ad revenue.** Mute plus beep, and the subtitle shows `F***`. Censor strong profanity (f-word and variants, s-word, c-word, mf, etc.), every slur, sexual terms and hard-drug references. Leave mild words (damn, hell, crap, sucks) alone. Read the whole transcript for these, including the cold open. Words the transcript missed but you can see in chat or context: add a `spans` entry.
5. **Hook text (ask first).** Write **10** options as plain statements that name the subject and hold back the payoff, e.g. "xQc tried to rob a gas station with a fish". No questions, no hype words ("insane", "crazy"), no fear or outrage bait. Ask the user to pick one, edit one, or have none. The chosen line becomes a `texts` entry at the top for the first ~2.5 s.

## 3. Effects (pick what the moment needs, not everything)

These are defaults from the "Stay or Swipe" Shorts research the user works from:
- **Open on motion and energy.** No intro, logo or loud slam at 0 s. The cold open does this.
- **A second beat before 10 s:** a punch-in, a sound or a meme lands between 5 and 10 s of the Short. That's when viewers swipe.
- **Punch-in zooms** (`scale` 1.15-1.4) on reactions and reveals, eased by default; `"ease": false` makes a hard punch on a hit. Use `band: "face"` to punch into the streamer's face only, `"game"` for the gameplay. Pace calm stretches with gentle punch-ins.
- **Shake + flash** only on the single biggest hit, once.
- **Grade:** commit to `warm` (funny, hype, wholesome) or `cold` (tense, scary, chaotic). Never leave a clip in between unless it already looks right.
- **One sound per beat change** (whoosh on a punch-in, boom on the reveal). The key spoken line never lands in the second right after a sound effect.
- **Never repeat the same trick back to back,** and keep it readable: one focal point at a time.
- **Memes:** 1-3 per Short, only where they land a punchline or mirror the reaction (the "bruh", "emotional damage", "vine boom" moments). Keep them short (0.8-2.5 s) and placed so they don't cover the streamer's face or the subtitle line. A meme with sound ducks the game under it automatically. Use a full-screen cut-away (`"full": true`) only for a reaction meme of 1.5 s or less, right after the payoff.

### Getting memes

```sh
clipper meme search bruh --kind sounds        # myinstants.com sound buttons
clipper meme search emotional damage          # short video memes with sound (YouTube, 40 s or less)
clipper meme search drake --kind images       # classic meme images (memegen.link, Imgflip)
clipper meme get <job> <clip> <id> [--top TEXT --bottom TEXT]   # saves into the clip's memes/ and prints the file
```

Each search line is `id<TAB>name`. Look at a video meme's frames before using it, and pick the 1-2 s that matter with `from`/`dur`. Memes come from other people. Mention this in the final report, because the user decides what's fine to post.

## 4. Write the edit

**`edit.json`:** change only the keys you mean to (layout, split, rects, hook, trims, subtitle style). Keep the rest.

**`fx.json`** (every key optional; the renderer checks it and says exactly what's wrong):

```json
{
  "grade": "warm",
  "texts":    [{"text": "HE TRIED TO ROB A GAS STATION WITH A FISH", "at": 0, "dur": 2.5, "y": 0.1, "color": "#FFFFFF", "size": 80}],
  "zooms":    [{"at": 6.2, "dur": 1.4, "scale": 1.3, "x": 0.5, "y": 0.6, "band": "game"},
               {"at": 9.0, "dur": 0.8, "scale": 1.25, "band": "face", "ease": false}],
  "shakes":   [{"at": 9.0, "dur": 0.4, "strength": 18}],
  "flashes":  [{"at": 9.0, "dur": 0.15, "color": "#FFFFFF"}],
  "sounds":   [{"src": "memes/20261117-vine-boom.mp3", "at": 9.0, "volume": 0.8}],
  "overlays": [{"src": "memes/20261117-bruh.mp4", "at": 11.2, "dur": 1.6, "from": 0.3, "x": 0.5, "y": 0.62, "w": 0.6, "rotate": -3},
               {"src": "memes/20261117-crying.png", "at": 14.0, "dur": 1.2, "full": true}],
  "censor":   {"words": ["fuck*", "shit*"], "spans": [{"start": 12.4, "end": 12.8}], "beep": true, "volume": 0.5},
  "emphasis": {"words": ["no", "way", "fish"], "color": "#FFD400", "scale": 1.25}
}
```

- **Times:** `at`/`dur` are seconds of the **finished Short**. The cold open plays first, so a moment at source second `s` in the body is at `hook length + s - trimStart` (the brief gives the numbers). A moment inside the hook window plays twice; time an effect for one of them.
- **Censor** is in **source** seconds: `words` matches transcript words (a trailing `*` = starts with), and `spans` covers anything else. It applies everywhere the moment plays (cold open and body).
- `overlays`: images or videos (`x`, `y` = centre, 0-1 of the frame; `w` = share of the width). Video memes play their own sound (`volume`); the game ducks to `duck` (0.25 by default) under them. `sounds`: `volume`, optional `duck`.
- Paths are relative to the clip folder (`memes/...`).

## 5. Render and check

1. If `final.mp4` exists, keep it: copy it to `final-v<N>.mp4` first.
2. `clipper render <job> <clip>`. It takes a minute or two. A wrong `fx.json` stops it with the reason; fix it and run again.
3. Look at the result. Pull frames from `final.mp4` at the cold open, each effect, each meme and the end, then check: the face band covers the top/bottom 30% with no border, subtitles are readable and not covered by a meme, memes don't cover the face, censored words show as `F***`. For audio, `ffmpeg -i final.mp4 -af ebur128=peak=true -f null -` shows loudness; aim for around -14 LUFS integrated. If it's far off: `ffmpeg -i final.mp4 -af loudnorm=I=-14:TP=-1:LRA=11 -c:v copy -c:a aac -b:a 320k final-norm.mp4`.
4. Fix anything wrong and render again before showing the user.

## 6. Hand back and refine

Tell the user, briefly:
- where the Short is (`jobs/<job>/clips/<clip>/final.mp4`, plus `final.srt`) and how long it is;
- the edit, as a short list with times: cold open, hook text, zooms, memes (with where each meme came from), sounds, what was censored;
- 2-3 specific things they might want different ("the meme at 11 s could be a sound instead").

Then ask what to change, and repeat steps 4-5 until they're happy. Keep earlier versions until they approve one.

If they ask for a **Shorts package**: write `package.md` in the clip folder with a title (a plain statement naming the streamer and what happens, no question, no clickbait), a description (2-3 natural sentences, credit the streamer and link the stream, put any comment prompt at the end; use the humanizer skill if it's available), and 5-8 tags. Upload to YouTube only when asked, and always as **private**: the user publishes after checking it.
