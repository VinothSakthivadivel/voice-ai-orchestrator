"""Server-side noise reduction: spectral gating / subtraction in numpy.

Uses ``noisereduce`` if it is installed (pip install noisereduce), otherwise a
built-in spectral subtraction with a noise profile learnt from non-speech
frames. Browser-side WebRTC noise suppression + AEC is always on in addition.
"""
from __future__ import annotations

from collections import deque

import numpy as np

try:  # optional
    import noisereduce as _nr  # type: ignore
except Exception:  # pragma: no cover
    _nr = None

N_FFT = 512
HOP = 128


def _stft(x: np.ndarray) -> np.ndarray:
    win = np.hanning(N_FFT).astype(np.float32)
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    n = 1 + (len(x) - N_FFT) // HOP
    frames = np.stack([x[i * HOP:i * HOP + N_FFT] * win for i in range(n)])
    return np.fft.rfft(frames, axis=1)


def _istft(S: np.ndarray, length: int) -> np.ndarray:
    win = np.hanning(N_FFT).astype(np.float32)
    frames = np.fft.irfft(S, n=N_FFT, axis=1) * win
    out = np.zeros(HOP * (len(frames) - 1) + N_FFT, dtype=np.float32)
    norm = np.zeros_like(out)
    for i, f in enumerate(frames):
        out[i * HOP:i * HOP + N_FFT] += f
        norm[i * HOP:i * HOP + N_FFT] += win * win
    out /= np.maximum(norm, 1e-6)
    return out[:length]


class NoiseReducer:
    def __init__(self, sample_rate: int = 16000, strength: float = 1.5, floor: float = 0.08,
                 profile_seconds: float = 1.5):
        self.sample_rate = sample_rate
        self.strength = strength
        self.floor = floor
        self._noise = deque(maxlen=int(profile_seconds * sample_rate / 320))

    @property
    def backend(self) -> str:
        return "noisereduce" if _nr else "spectral-subtraction"

    def add_noise_frame(self, pcm16: bytes):
        self._noise.append(pcm16)

    def reduce(self, pcm16: bytes) -> bytes:
        x = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        if x.size < N_FFT:
            return pcm16
        noise = (np.frombuffer(b"".join(self._noise), dtype="<i2").astype(np.float32) / 32768.0
                 if self._noise else None)
        if _nr is not None:
            y = _nr.reduce_noise(y=x, sr=self.sample_rate, y_noise=noise, stationary=noise is not None)
        else:
            S = _stft(x)
            pw = np.abs(S) ** 2
            if noise is not None and noise.size >= N_FFT:
                npw = (np.abs(_stft(noise)) ** 2).mean(axis=0)
            else:  # estimate from quietest 10% of frames
                e = pw.sum(axis=1)
                q = pw[e <= np.quantile(e, 0.1)]
                npw = q.mean(axis=0) if len(q) else pw.min(axis=0)
            # power spectral subtraction (over-subtraction) -> gain mask
            gain = np.sqrt(np.maximum(1.0 - (self.strength * 2.0) * npw / (pw + 1e-12), self.floor ** 2))
            # smooth the mask over time and frequency to avoid "musical noise"
            k = np.ones(3) / 3
            gain = np.apply_along_axis(lambda g: np.convolve(g, k, "same"), 1, gain)
            gain = np.apply_along_axis(lambda g: np.convolve(g, k, "same"), 0, gain)
            y = _istft(S * gain, len(x))
        y = np.clip(y, -1, 1)
        return (y * 32767).astype("<i2").tobytes()
