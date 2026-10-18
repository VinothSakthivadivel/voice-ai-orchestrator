"""Primary-speaker verification ("ignore other voices in a crowd").

A pluggable ``SpeakerEmbedder`` turns an utterance into a fixed vector. The
default ``MFCCEmbedder`` (numpy only) uses MFCC + pitch statistics - cheap and
dependency free but only reliable for clearly different voices.  Plug in a
neural embedder (e.g. resemblyzer / SpeechBrain ECAPA) by subclassing
``SpeakerEmbedder`` and registering it in ``EMBEDDERS``.

``SpeakerGate`` enrolls the primary speaker (first N seconds of speech, or on
demand) and rejects utterances whose cosine similarity to the enrolled
voiceprint is below ``threshold``.  Provider diarization (Deepgram,
AssemblyAI, Google) is used additionally via ``filter_words_by_speaker``.
"""
from __future__ import annotations

import numpy as np

SR = 16000


def _mel_filterbank(n_mels=40, n_fft=512, sr=SR, fmin=60, fmax=7600):
    def hz2mel(h): return 2595 * np.log10(1 + h / 700)
    def mel2hz(m): return 700 * (10 ** (m / 2595) - 1)
    mels = np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2)
    bins = np.floor((n_fft + 1) * mel2hz(mels) / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for i in range(1, n_mels + 1):
        l, c, r = bins[i - 1], bins[i], bins[i + 1]
        for k in range(l, c):
            fb[i - 1, k] = (k - l) / max(c - l, 1)
        for k in range(c, r):
            fb[i - 1, k] = (r - k) / max(r - c, 1)
    return fb


_FB = _mel_filterbank()
_DCT = np.cos(np.pi / 40 * (np.arange(40)[None, :] + 0.5) * np.arange(20)[:, None]).astype(np.float32)


class SpeakerEmbedder:
    name = "base"

    def embed(self, pcm16: bytes) -> np.ndarray | None:
        raise NotImplementedError


class MFCCEmbedder(SpeakerEmbedder):
    name = "mfcc-stats"

    def embed(self, pcm16: bytes) -> np.ndarray | None:
        x = np.frombuffer(pcm16, dtype="<i2").astype(np.float32) / 32768.0
        if x.size < SR * 0.3:
            return None
        x = np.append(x[0], x[1:] - 0.97 * x[:-1])
        n, hop = 512, 160
        win = np.hamming(n).astype(np.float32)
        nf = 1 + (len(x) - n) // hop
        frames = np.stack([x[i * hop:i * hop + n] * win for i in range(nf)])
        energy = (frames ** 2).sum(axis=1)
        keep = energy > np.quantile(energy, 0.4)  # voiced-ish frames only
        frames = frames[keep]
        if len(frames) < 10:
            return None
        spec = np.abs(np.fft.rfft(frames, axis=1)) ** 2
        logmel = np.log(spec @ _FB.T + 1e-8)
        mfcc = logmel @ _DCT.T  # (frames, 20)
        mfcc = mfcc[:, 1:]  # drop energy term
        # crude pitch via autocorrelation on voiced frames
        f0s = []
        for f in frames[:: max(1, len(frames) // 40)]:
            ac = np.correlate(f, f, "full")[n - 1:]
            lo, hi = SR // 400, SR // 70
            k = lo + int(np.argmax(ac[lo:hi]))
            if ac[k] > 0.3 * ac[0]:
                f0s.append(SR / k)
        f0 = np.array(f0s) if f0s else np.array([0.0])
        vec = np.concatenate([mfcc.mean(0), mfcc.std(0), [np.log1p(np.median(f0)) * 4, np.std(np.log1p(f0)) * 4]])
        return vec.astype(np.float32)


EMBEDDERS: dict[str, type[SpeakerEmbedder]] = {"mfcc-stats": MFCCEmbedder}


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


class SpeakerGate:
    def __init__(self, enabled: bool = False, threshold: float = 0.80, enroll_seconds: float = 3.0,
                 embedder: str = "mfcc-stats"):
        self.enabled = enabled
        self.threshold = threshold
        self.enroll_seconds = enroll_seconds
        self.embedder = EMBEDDERS.get(embedder, MFCCEmbedder)()
        self._samples: list[np.ndarray] = []
        self._enrolled_sec = 0.0
        self.voiceprint: np.ndarray | None = None

    @property
    def enrolled(self) -> bool:
        return self.voiceprint is not None

    def reset_enrollment(self):
        self._samples.clear(); self._enrolled_sec = 0.0; self.voiceprint = None

    def enroll(self, pcm16: bytes) -> bool:
        e = self.embedder.embed(pcm16)
        if e is None:
            return False
        self._samples.append(e)
        self._enrolled_sec += len(pcm16) / 2 / SR
        self.voiceprint = np.mean(self._samples, axis=0)
        return True

    def check_only(self, pcm16: bytes) -> tuple[bool, float | None]:
        """Verify without enrolling (used for barge-in decisions on partial audio)."""
        if not self.enabled or self.voiceprint is None:
            return True, None
        e = self.embedder.embed(pcm16)
        if e is None:
            return True, None
        sim = cosine(e, self.voiceprint)
        return sim >= self.threshold, sim

    def check(self, pcm16: bytes) -> tuple[bool, float | None]:
        """Return (accepted, similarity). Auto-enrolls until enroll_seconds reached."""
        if not self.enabled:
            return True, None
        if self._enrolled_sec < self.enroll_seconds:
            e = self.embedder.embed(pcm16)
            sim = cosine(e, self.voiceprint) if (e is not None and self.voiceprint is not None) else None
            if e is not None:
                self._samples.append(e)
                self._enrolled_sec += len(pcm16) / 2 / SR
                self.voiceprint = np.mean(self._samples, axis=0)
            return True, sim
        e = self.embedder.embed(pcm16)
        if e is None or self.voiceprint is None:
            return True, None
        sim = cosine(e, self.voiceprint)
        return sim >= self.threshold, sim


def filter_words_by_speaker(words: list[dict], primary: int | str | None) -> tuple[str, int | str | None]:
    """Keep words of the primary diarized speaker. If unknown, primary = most frequent speaker."""
    labeled = [w for w in words if w.get("speaker") is not None]
    if not labeled:
        return " ".join(w["word"] for w in words if w.get("word")), primary
    if primary is None:
        counts: dict = {}
        for w in labeled:
            counts[w["speaker"]] = counts.get(w["speaker"], 0) + 1
        primary = max(counts, key=counts.get)
    return " ".join(w["word"] for w in labeled if w["speaker"] == primary and w.get("word")), primary
