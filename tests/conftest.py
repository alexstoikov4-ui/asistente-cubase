import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def fs():
    return 44100


def sine(freq, dur, fs, amp=0.5, phase=0.0):
    t = np.arange(int(dur * fs)) / fs
    return amp * np.sin(2 * np.pi * freq * t + phase)


def kick_track(bpm, dur, fs, amp=0.9):
    n = int(dur * fs)
    out = np.zeros(n)
    k = int(0.25 * fs)
    t = np.arange(k) / fs
    hit = np.sin(2 * np.pi * np.cumsum(50 + 100 * np.exp(-t * 30)) / fs) * np.exp(-t * 10) * amp
    step = 60.0 / bpm
    pos = 0.0
    while pos * fs < n:
        s = int(pos * fs)
        e = min(n, s + k)
        out[s:e] += hit[:e - s]
        pos += step
    return out
