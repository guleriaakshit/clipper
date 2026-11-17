"""What a clip can be found by. Any mix of three signals, chosen per stream:

- chat:   chat runs at chat_x times the stream's average rate or more (2x by default; spikes.py; needs the chat);
- audio:  the streamer gets loud: their voice reaches loud_x times their usual level (2x = +6 dB by default). The usual
          level is the median loudness of the whole stream (so far, when live), leaving out silence;
- speech: what the streamer says: reaction phrases ("no way", "oh my god", "let's go"...) or words you ask for.

Every signal becomes a 0-1 "excitement" value per 5 s bin, lined up on the moment itself (chat reacts about
10 s late, so the chat series is moved 10 s earlier). A moment is any stretch where a chosen signal fires;
its score out of 100 mixes the strongest signal (60 %) with the average of the chosen ones (40 %), so
moments where several signals agree rank higher. Chat-only keeps spikes.analyse's own scoring.
"""
import re
import subprocess

import numpy as np

from . import spikes

BIN = spikes.BIN
NAMES = {"chat": "Chat spikes", "audio": "Loud voice", "speech": "What's said"}
CHAT_LAG_BINS = 2                 # chat reacts ~10 s after the moment
CHAT_X = spikes.SPIKE_X           # default: chat at 2x its average
LOUD_X = 2.0                      # default: voice at 2x its usual level (+6 dB)
QUIET_DB = -55.0                  # bins quieter than this are silence, left out of the usual level
SR = 16000
REACT = re.compile(r"\b(oh my god|oh my gosh|no way|no no+|what the (?:hell|heck|fuck)|wtf|let'?s go+|holy (?:shit|crap|cow)|"
                   r"are you (?:kidding|serious)|bro+|dude|clip (?:that|it)|oh no|insane|crazy|unbelievable|"
                   r"i can'?t believe|get out|shut up|yes+ yes+|what+ ?\?|wait wait|oh shit|hell no|hell yeah)\b", re.I)


# ---------------- audio + speech from a stream of samples ----------------
class AudioScan:
    """Feed 16 kHz mono float32 audio in order. Keeps the loudest 0.5 s level per 5 s bin (dB) and, if asked,
    the spoken words (transcribed in 30 s pieces)."""

    def __init__(self, speech=False, t0=0.0):
        self.db, self.words, self.speech = [], [], speech
        self.t = t0
        self._lvl = np.zeros(0, np.float32)
        self._talk, self._talk_t = [], t0

    def feed(self, x):
        x = np.asarray(x, np.float32)
        self._lvl = np.concatenate([self._lvl, x])
        n = int(BIN * SR)
        while len(self._lvl) >= n:
            self._bin(self._lvl[:n])
            self._lvl = self._lvl[n:]
        if self.speech:
            self._talk.append(x)
            if sum(len(a) for a in self._talk) >= 30 * SR:
                self._transcribe()

    def _bin(self, a):
        h = SR // 2
        rms = [float(np.sqrt(np.mean(a[i:i + h] ** 2) + 1e-12)) for i in range(0, len(a) - h + 1, h)]
        self.db.append(round(20 * np.log10(max(rms) if rms else 1e-6), 2))

    def _transcribe(self):
        from . import stt
        x = np.concatenate(self._talk) if self._talk else np.zeros(0, np.float32)
        self.words += stt.words_from_array(x, self._talk_t)
        self._talk_t += len(x) / SR
        self._talk = []

    def finish(self):
        if len(self._lvl) >= SR:
            self._bin(self._lvl)
        self._lvl = np.zeros(0, np.float32)
        if self.speech and self._talk:
            self._transcribe()


def segment_audio(path):
    """16 kHz mono float32 samples of one recorded segment (live)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw[: len(raw) // 4 * 4], np.float32)


# ---------------- per-bin excitement ----------------
def _fit(a, n, fill=0.0):
    a = list(a)[:n]
    return np.array(a + [fill] * (n - len(a)), float)


def chat_excite(msgs, duration, n, chat_x=CHAT_X):
    counts, smooth, avg = spikes.rate(msgs, duration)
    x = _fit(smooth / avg if avg > 0 else smooth * 0, n + CHAT_LAG_BINS)[CHAT_LAG_BINS:]
    return np.clip((x - 1) / 5, 0, 1), x >= chat_x


def usual_db(db):
    """The streamer's usual voice level: median loudness of the stream, leaving out silence."""
    d = np.asarray(db, float)
    sound = d[d > QUIET_DB]
    return float(np.median(sound)) if len(sound) else float(np.median(d)) if len(d) else -30.0


def loud_factor(db_value, usual):
    """How many times their usual level (amplitude): +6 dB = 2x, +9.5 dB = 3x, +12 dB = 4x."""
    return 10 ** ((db_value - usual) / 20)


def audio_excite(db, n, loud_x=LOUD_X):
    d = _fit(db, n, fill=-90.0)
    usual = usual_db(db)
    above = d - usual
    need = 20 * np.log10(max(1.01, loud_x))                 # dB above usual for loud_x
    return np.clip((above - need / 2) / max(3.0, need), 0, 1), above >= need, 10 ** (above / 20)


def speech_excite(words, n, keywords=""):
    keys = [k.strip().lower() for k in re.split(r"[,\n]", keywords or "") if k.strip()]
    hits, said = np.zeros(n), [[] for _ in range(n)]
    for w_i, w in enumerate(words):
        i = min(n - 1, int(w["start"] // BIN))
        # a phrase starts at this word: look at the next few words
        span = " ".join(x["text"] for x in words[w_i:w_i + 5]).lower()
        span = re.sub(r"[^\w' ?]", "", span)
        m = REACT.match(span)
        if m:
            hits[i] += 1
            said[i].append(m.group(0))
        for k in keys:
            if span.startswith(k):
                hits[i] += 3
                said[i].append(k)
    roll = np.convolve(hits, np.ones(2), mode="full")[:n]          # this bin + the one before
    return np.clip(roll / 3, 0, 1), roll >= 2, said


# ---------------- moments ----------------
def find(enabled, duration, msgs=None, db=None, words=None, keywords="", top_n=10, min_score=0, chat_x=CHAT_X,
         loud_x=LOUD_X):
    """Moments scoring at least min_score, best first. res["below"] = the best one that didn't make it.
    chat_x / loud_x: how many times its usual level chat / the streamer's voice must reach to count as a spike."""
    enabled = [s for s in ("chat", "audio", "speech") if s in (enabled or [])] or ["chat"]
    chat_x, loud_x = float(chat_x or CHAT_X), float(loud_x or LOUD_X)
    if enabled == ["chat"]:
        res = spikes.analyse(msgs, duration, top_n=10_000, spike_x=chat_x)
        for c in res["clips"]:
            c["basis"], c["sub"], c["said"], c["hot_ref"] = ["chat"], {"chat": c["score"]}, [], c["chat_peak"]
        res = _keep(res, min_score, top_n)
    else:
        res = _keep(_find(enabled, duration, msgs, db, words, keywords, chat_x, loud_x), min_score, top_n)
    res["chat_x"], res["loud_x"] = chat_x, loud_x
    return res


def _keep(res, min_score, top_n):
    good = [c for c in res["clips"] if c["score"] >= min_score]
    bad = [c for c in res["clips"] if c["score"] < min_score]
    res["below"] = ({"score": bad[0]["score"], "start": bad[0]["start"], "basis": bad[0]["basis"], "count": len(bad)}
                    if bad else None)
    res["clips"] = good[:top_n]
    for i, c in enumerate(res["clips"], 1):
        c["id"] = i
    res["min_score"] = min_score
    return res


def _find(enabled, duration, msgs, db, words, keywords, chat_x=CHAT_X, loud_x=LOUD_X):
    n = int(np.ceil(duration / BIN)) or 1
    E, T, said, factor = {}, {}, [[] for _ in range(n)], None
    if "chat" in enabled and msgs:
        E["chat"], T["chat"] = chat_excite(msgs, duration, n, chat_x)
    if "audio" in enabled and db:
        E["audio"], T["audio"], factor = audio_excite(db, n, loud_x)
    if "speech" in enabled and words:
        E["speech"], T["speech"], said = speech_excite(words, n, keywords)
    if not E:
        return {"bin": BIN, "avg": 0, "counts": [], "clips": []}
    mask = np.zeros(n, bool)
    for t in T.values():
        mask |= t
    avg_e = np.mean([E[s] for s in E], axis=0)
    cands = []
    for a, b in spikes._regions(mask):
        lo, hi = max(0, a - 1), min(n, b + 2)
        pk = lo + int(np.argmax(avg_e[lo:hi]))
        sub = {s: int(round(100 * float(E[s][lo:hi].max()))) for s in E}
        vals = list(sub.values())
        score = round(0.6 * max(vals) + 0.4 * sum(vals) / len(vals))
        start = max(0.0, a * BIN - 10.0)
        end = min(duration, (b + 1) * BIN + 8.0)
        if end - start < spikes.MIN_LEN:
            end = min(duration, start + spikes.MIN_LEN)
        if end - start > spikes.MAX_LEN:
            start = max(0.0, min(start, pk * BIN - 20.0))
            end = start + spikes.MAX_LEN
        phrases = [p for i in range(lo, hi) for p in said[i]] if said else []
        cands.append({"start": round(start, 2), "end": round(end, 2), "peak": pk * BIN + BIN / 2, "score": score,
                      "basis": [s for s in E if T[s][a:b + 1].any()], "sub": sub, "said": list(dict.fromkeys(phrases))[:6],
                      "loud_x": round(float(factor[lo:hi].max()), 1) if factor is not None else None})
    cands.sort(key=lambda c: -c["score"])
    kept = []
    for c in cands:
        if all(abs(c["peak"] - k["peak"]) >= spikes.DEDUPE for k in kept):
            kept.append(c)

    counts, smooth, avg = spikes.rate(msgs, duration) if msgs else (np.zeros(n), np.zeros(n), 0.0)
    times = np.array([m["t"] for m in msgs]) if msgs else np.zeros(0)
    for i, c in enumerate(kept, 1):
        c["id"] = i
        c["hot_ref"] = c["peak"] + 8.0             # hotspot looks 15 s to 2 s before this
        c.update(_chat_facts(msgs, times, smooth, avg, c))
        c["auto_reason"] = reason(c)
    return {"bin": BIN, "avg": float(avg), "avg_per_min": round(avg * 60 / BIN, 2) if avg else 0,
            "usual_db": round(usual_db(db), 1) if db else None,
            "counts": [round(float(x), 2) for x in smooth] if msgs else [], "clips": kept}


def _chat_facts(msgs, times, smooth, avg, c):
    """The chat numbers the dashboard shows, for any moment (zeros when there's no chat)."""
    empty = {"chat_start": c["start"], "chat_end": c["end"], "chat_peak": c["hot_ref"], "multiple": 0.0, "peak_per_min": 0.0,
             "avg_per_min": 0.0, "z": 0.0, "messages": 0, "unique_chatters": 0,
             "signals": {k: 0 for k in spikes.SIGNALS}, "reaction_share": 0.0, "top_words": [], "sample_chat": []}
    if not msgs or avg <= 0:
        return empty
    a, b = c["start"], c["end"] + 10.0                                 # chat answers ~10 s late
    lo, hi = np.searchsorted(times, a), np.searchsorted(times, b)
    w = msgs[lo:hi]
    i0, i1 = int(a // BIN), max(int(a // BIN) + 1, int(b // BIN))
    seg = smooth[i0:i1] if i1 <= len(smooth) else smooth[i0:]
    pk = float(seg.max()) if len(seg) else 0.0
    texts = [m["text"] for m in w]
    sig = {k: sum(1 for t in texts if rx.search(t)) for k, rx in spikes.SIG_RX.items()}
    step = max(1, len(w) // 40)
    return {"chat_start": a, "chat_end": b, "chat_peak": (i0 + int(np.argmax(seg))) * BIN + BIN / 2 if len(seg) else c["hot_ref"],
            "multiple": round(pk / avg, 1), "peak_per_min": round(pk * 60 / BIN, 1), "avg_per_min": round(avg * 60 / BIN, 1),
            "z": 0.0, "messages": len(w), "unique_chatters": len({m["user"] for m in w}), "signals": sig,
            "reaction_share": round(sum(sig.values()) / max(1, len(texts)), 2),
            "top_words": [x for x, _ in spikes._tokens(texts).most_common(10)],
            "sample_chat": [f'{m["user"]}: {m["text"]}' for m in w[::step]][:40]}


def reason(c):
    parts = []
    if "chat" in c["basis"] or c["multiple"] >= spikes.SPIKE_X:
        parts.append(f'Chat hit {c["multiple"]}x its average ({c["peak_per_min"]:.0f} vs {c["avg_per_min"]:.0f} msgs/min) '
                     f'with {c["unique_chatters"]} different chatters.')
    if "audio" in c["basis"]:
        parts.append(f'The streamer got loud: {c["loud_x"]}x their usual voice level.' if c.get("loud_x")
                     else f'The streamer got loud (voice score {c["sub"].get("audio", 0)}/100).')
    if c.get("said"):
        parts.append("They said: " + ", ".join(f'"{s}"' for s in c["said"][:4]) + ".")
    if c["top_words"]:
        parts.append("Chat kept saying: " + ", ".join(c["top_words"][:6]) + ".")
    return " ".join(parts) or "Picked by " + ", ".join(NAMES[s].lower() for s in c["basis"]) + "."
