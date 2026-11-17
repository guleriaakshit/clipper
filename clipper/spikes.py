"""Chat rate -> average -> spikes -> scored clip windows."""
import re
from collections import Counter

import numpy as np

BIN = 5.0             # seconds per bin
SPIKE_X = 2.0         # a spike = smoothed rate >= 2x the stream average
END_X = 1.5           # the clip ends when chat calms below 1.5x average
LEAD = 20.0           # chat reacts ~5-15 s after the moment: start the clip this far before the spike
TAIL = 3.0
MIN_LEN, MAX_LEN = 20.0, 60.0
DEDUPE = 60.0         # two spikes closer than this are one moment

SIGNALS = {
    "laugh": r"\b(lul|lulw|kekw|kek|omegalul|lmao+|lmfao|rofl|haha\w*|xd+|icant|💀|😂|🤣|lol+)\b|💀|😂|🤣",
    "hype": r"\b(pog\w*|poggers|w+|lets go+|let'?s go+|hype|goat|insane|clean|cracked|ez|gg)\b|!{2,}|🔥",
    "shock": r"\b(wtf|what+|omg|no way|holy|monkas|wait+|bro+|huh+|nah+)\b|\?{2,}|😱|😳",
    "clip": r"\bclip\b|clip it|clipped|\bclippers?\b",
    "sad": r"\b(f+|rip|sadge|pain|noo+|l+)\b|😭|😢",
}
SIG_RX = {k: re.compile(v, re.I) for k, v in SIGNALS.items()}
SIG_LABEL = {"laugh": "laughter", "hype": "hype", "shock": "shock/disbelief", "clip": "'clip it' calls", "sad": "sad/F reactions"}
STOP = set("the a an and or to of in on is it i you he she we they this that for with be are was was im i'm u ur my me so just at do dont don't not no yes ya yeah like get got have has what".split())


def rate(msgs, duration):
    n = int(np.ceil(max(duration, msgs[-1]["t"] + BIN) / BIN))
    counts = np.zeros(n)
    for m in msgs:
        counts[min(n - 1, int(m["t"] // BIN))] += 1
    smooth = np.convolve(counts, np.ones(3) / 3, mode="same")
    avg = float(counts.mean())
    return counts, smooth, avg


def _rolling_median(x, w):
    half = w // 2
    out = np.empty_like(x)
    for i in range(len(x)):
        out[i] = np.median(x[max(0, i - half): i + half + 1])
    return out


def _regions(mask):
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and (mask[j + 1] or (j + 2 < n and mask[j + 2])):  # bridge 1-bin dips
                j += 1
            out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def _tokens(texts):
    c = Counter()
    for t in texts:
        for w in set(re.findall(r"[\w']+|[^\w\s]{1,3}", t.lower())):
            if w not in STOP and len(w) > 1 or w in ("w", "l", "f"):
                c[w] += 1
    return c


def analyse(msgs, duration, top_n=10, spike_x=SPIKE_X):
    """spike_x: chat must reach this many times the stream's average rate."""
    counts, smooth, avg = rate(msgs, duration)
    if avg <= 0:
        return {"bin": BIN, "avg": 0, "counts": [], "clips": []}
    base = _rolling_median(smooth, int(600 / BIN))
    mad = _rolling_median(np.abs(smooth - base), int(600 / BIN)) * 1.4826 + 0.5
    times = np.array([m["t"] for m in msgs])
    avg_unique_per_bin = np.mean([len({m["user"] for m in msgs[a:b]}) for a, b in
                                  zip(np.searchsorted(times, np.arange(0, len(counts)) * BIN),
                                      np.searchsorted(times, np.arange(1, len(counts) + 1) * BIN))] or [1])
    cands = []
    for a, b in _regions(smooth >= spike_x * avg):
        pk = a + int(np.argmax(smooth[a:b + 1]))
        r0, r1 = a * BIN, (b + 1) * BIN
        lo, hi = np.searchsorted(times, r0), np.searchsorted(times, r1)
        window_msgs = msgs[lo:hi]
        texts = [m["text"] for m in window_msgs]
        uniq = len({m["user"] for m in window_msgs})
        sig = {k: sum(1 for t in texts if rx.search(t)) for k, rx in SIG_RX.items()}
        share = sum(sig.values()) / max(1, len(texts))
        multiple = float(smooth[pk] / avg)
        z = float((smooth[pk] - base[pk]) / mad[pk])
        uniq_ratio = uniq / max(1e-6, avg_unique_per_bin * (b - a + 1))
        dur = r1 - r0
        score = (min(multiple / 6, 1) * 40 + min(max(z, 0) / 8, 1) * 20 + min(uniq_ratio / 4, 1) * 15
                 + min(dur / 30, 1) * 10 + min(share / 0.5, 1) * 15)
        # clip window
        start = max(0.0, r0 - LEAD)
        e = b + 1
        while e < len(smooth) and smooth[e] >= END_X * avg:
            e += 1
        end = min(duration, e * BIN + TAIL)
        if end - start < MIN_LEN:
            end = min(duration, start + MIN_LEN)
        if end - start > MAX_LEN:
            end = start + MAX_LEN
        top = [w for w, _ in _tokens(texts).most_common(10)]
        step = max(1, len(window_msgs) // 40)
        cands.append({
            "chat_start": r0, "chat_end": r1, "chat_peak": pk * BIN + BIN / 2,
            "start": round(start, 2), "end": round(end, 2),
            "multiple": round(multiple, 1), "peak_per_min": round(smooth[pk] * 60 / BIN, 1),
            "avg_per_min": round(avg * 60 / BIN, 1), "z": round(z, 1),
            "messages": len(window_msgs), "unique_chatters": uniq,
            "signals": sig, "reaction_share": round(share, 2), "top_words": top,
            "sample_chat": [f'{m["user"]}: {m["text"]}' for m in window_msgs[::step]][:40],
            "score": round(score),
        })
    cands.sort(key=lambda c: -c["score"])
    kept = []
    for c in cands:
        if all(abs(c["chat_peak"] - k["chat_peak"]) >= DEDUPE for k in kept):
            kept.append(c)
        if len(kept) >= top_n:
            break
    for i, c in enumerate(kept, 1):
        c["id"] = i
        c["auto_reason"] = auto_reason(c)
    return {"bin": BIN, "avg": avg, "avg_per_min": round(avg * 60 / BIN, 2),
            "counts": [round(float(x), 2) for x in smooth], "clips": kept}


def auto_reason(c):
    """Rule-based summary shown until /clip writes the real 'what' and 'why'."""
    sig = sorted(((v, k) for k, v in c["signals"].items() if v), reverse=True)
    parts = [f'Chat hit {c["multiple"]}x its average ({c["peak_per_min"]:.0f} vs {c["avg_per_min"]:.0f} msgs/min) '
             f'with {c["unique_chatters"]} different chatters.']
    if sig:
        parts.append("Main reaction: " + ", ".join(f"{SIG_LABEL[k]} ({v})" for v, k in sig[:3]) + ".")
    if c["top_words"]:
        parts.append("Chat kept saying: " + ", ".join(c["top_words"][:6]) + ".")
    return " ".join(parts)
