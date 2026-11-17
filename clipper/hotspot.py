"""Find the clip's best 2-4 s moment for the cold open: the loudest stretch just before chat exploded."""
import re
import subprocess

import numpy as np


def loudness(path):
    """Momentary loudness (LUFS) every 0.1 s."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    t, m = [], []
    for line in p.stderr.splitlines():
        r = re.search(r"t:\s*([\d.]+)\s+TARGET.*?M:\s*(-?[\d.]+|-inf)", line)
        if r:
            t.append(float(r.group(1)))
            m.append(-70.0 if r.group(2) == "-inf" else float(r.group(2)))
    return np.array(t), np.array(m)


def find(path, clip_start, chat_peak, clip_len, hook_len=3.0):
    """Returns (hook_start, hook_end) in seconds relative to the clip."""
    peak = chat_peak - clip_start                  # chat peak, relative to the clip
    lo, hi = max(0.0, peak - 15.0), max(hook_len, min(clip_len, peak - 2.0))
    t, m = loudness(path)
    if len(t) < 10:
        s = max(0.0, min(clip_len - hook_len, peak - 8.0))
        return round(s, 2), round(s + hook_len, 2)
    win = int(hook_len / 0.1)
    best_s, best_v = lo, -1e9
    for i in range(len(t)):
        s = t[i] - 0.4                             # ebur128 momentary is a 0.4 s trailing window
        if s < lo or s + hook_len > hi + 1e-6:
            continue
        v = float(np.mean(m[i:i + win]))
        if v > best_v:
            best_s, best_v = s, v
    best_s = max(0.0, min(best_s, clip_len - hook_len))
    return round(best_s, 2), round(best_s + hook_len, 2)
