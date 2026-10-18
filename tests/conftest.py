import os
import tempfile

import numpy as np
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="vao-test-"))
SR = 16000


def silence(sec, level=0.0005, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(0, level, int(SR * sec))
    return (x * 32767).astype("<i2").tobytes()


def voice(sec, f0=140.0, formants=(700, 1200, 2600), amp=0.3, seed=1):
    """Crude vowel-like harmonic signal with formant weighting and slight vibrato."""
    t = np.arange(int(SR * sec)) / SR
    f = f0 * (1 + 0.02 * np.sin(2 * np.pi * 5 * t))
    phase = 2 * np.pi * np.cumsum(f) / SR
    sig = np.zeros_like(t)
    for h in range(1, 30):
        fh = f0 * h
        if fh > 7500:
            break
        w = sum(np.exp(-((fh - F) / 150) ** 2) for F in formants) + 0.05
        sig += w * np.sin(h * phase) / h ** 0.5
    sig = sig / (np.abs(sig).max() + 1e-9) * amp
    sig *= 0.7 + 0.3 * np.sin(2 * np.pi * 3 * t) ** 2  # syllable envelope
    rng = np.random.default_rng(seed)
    sig += rng.normal(0, 0.002, len(t))
    return (sig * 32767).astype("<i2").tobytes()


def frames(pcm, n=640):
    return [pcm[i:i + n] for i in range(0, len(pcm) - n + 1, n)]


class FakeWire:
    def __init__(self):
        self.json, self.bytes = [], []

    async def send_json(self, m):
        self.json.append(m)

    async def send_bytes(self, b):
        self.bytes.append(b)

    def types(self):
        return [m["type"] for m in self.json]

    def of(self, t):
        return [m for m in self.json if m["type"] == t]


@pytest.fixture
def wire():
    return FakeWire()
