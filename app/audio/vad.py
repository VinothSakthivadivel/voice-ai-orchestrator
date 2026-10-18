"""Voice activity detection + turn detection (endpointing).

``EnergyVAD`` is an adaptive-noise-floor energy detector with zero dependencies.
If ``webrtcvad`` (pip install webrtcvad-wheels) is installed, ``mode="webrtc"``
combines it with the energy gate for better robustness in noisy rooms.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

try:  # optional
    import webrtcvad  # type: ignore
except Exception:  # pragma: no cover
    webrtcvad = None


def frame_db(pcm16: bytes) -> float:
    x = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
    if x.size == 0:
        return -100.0
    rms = float(np.sqrt(np.mean(x * x)) + 1e-9)
    return max(-100.0, 20 * np.log10(rms))


class EnergyVAD:
    def __init__(self, sample_rate: int = 16000, margin_db: float = 10.0, min_db: float = -50.0,
                 mode: str = "energy", aggressiveness: int = 2):
        self.sample_rate = sample_rate
        self.margin_db = margin_db
        self.min_db = min_db
        self.noise_floor = -60.0
        self.mode = mode
        self._webrtc = None
        if mode == "webrtc" and webrtcvad is not None:
            self._webrtc = webrtcvad.Vad(int(aggressiveness))
        self.last_db = -100.0
        self._hist: deque[float] = deque(maxlen=150)  # ~3 s of frame energies

    @property
    def backend(self) -> str:
        return "webrtc+energy" if self._webrtc else "energy"

    def is_speech(self, pcm16: bytes) -> bool:
        db = frame_db(pcm16)
        self.last_db = db
        # noise floor = low percentile of recent energies (minimum statistics) blended with an
        # exponential tracker, so it follows both quiet rooms and steady loud noise (fans, traffic).
        self._hist.append(db)
        if len(self._hist) >= 10:
            pct = float(np.percentile(self._hist, 10))
            self.noise_floor = 0.8 * self.noise_floor + 0.2 * pct if pct > self.noise_floor else pct
        thresh = max(self.noise_floor + self.margin_db, self.min_db)
        speech = db > thresh
        if speech and self._webrtc is not None and len(pcm16) in (320, 640, 960) and self.sample_rate == 16000:
            try:
                speech = self._webrtc.is_speech(pcm16, self.sample_rate)
            except Exception:
                pass
        if not speech and len(self._hist) < 10:
            a = 0.3 if db < self.noise_floor else 0.1
            self.noise_floor = (1 - a) * self.noise_floor + a * db
        return bool(speech)


@dataclass
class TurnEvent:
    kind: str  # "speech_start" | "speech_end"
    audio: bytes = b""
    speech_ms: int = 0


@dataclass
class TurnDetector:
    frame_ms: int = 20
    min_speech_ms: int = 200     # consecutive speech before "speech_start"
    end_silence_ms: int = 700    # trailing silence that ends a turn
    preroll_ms: int = 300        # audio kept before speech start
    max_utterance_ms: int = 30000
    _pre: list[bytes] = field(default_factory=list)
    _buf: list[bytes] = field(default_factory=list)
    in_speech: bool = False
    _speech_run: int = 0
    _silence_run: int = 0
    speech_ms: int = 0

    def reset(self):
        self._pre.clear(); self._buf.clear()
        self.in_speech = False
        self._speech_run = self._silence_run = self.speech_ms = 0

    @property
    def current_audio(self) -> bytes:
        return b"".join(self._buf)

    def feed(self, frame: bytes, is_speech: bool) -> list[TurnEvent]:
        ev: list[TurnEvent] = []
        if not self.in_speech:
            self._pre.append(frame)
            max_pre = max(1, (self.preroll_ms + self.min_speech_ms) // self.frame_ms)
            if len(self._pre) > max_pre:
                self._pre.pop(0)
            if is_speech:
                self._speech_run += self.frame_ms
            else:
                self._speech_run = max(0, self._speech_run - self.frame_ms)  # tolerate 1-frame gaps
            if self._speech_run >= self.min_speech_ms:
                self.in_speech = True
                self._buf = list(self._pre)
                self._pre.clear()
                self.speech_ms = self._speech_run
                self._silence_run = 0
                ev.append(TurnEvent("speech_start", speech_ms=self.speech_ms))
            return ev
        self._buf.append(frame)
        if is_speech:
            self.speech_ms += self.frame_ms
            self._silence_run = 0
        else:
            self._silence_run += self.frame_ms
        total = len(self._buf) * self.frame_ms
        if self._silence_run >= self.end_silence_ms or total >= self.max_utterance_ms:
            audio = b"".join(self._buf)
            sp = self.speech_ms
            self.reset()
            ev.append(TurnEvent("speech_end", audio=audio, speech_ms=sp))
        return ev
