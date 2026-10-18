"""Mock providers: zero-key demo + tests. Fully offline."""
from __future__ import annotations

import asyncio

import numpy as np

from .base import (AudioChunk, LLMProvider, ProviderMeta, STTProvider, STTResult, TTSProvider, pcm_to_wav,
                   register)


@register
class MockSTT(STTProvider):
    meta = ProviderMeta("stt", "mock", "Mock STT (offline demo)", [], features=["no-key"],
                        notes="Returns a canned transcript (or options.mock_text).")

    async def transcribe(self, pcm16, sample_rate, language, options):
        await asyncio.sleep(options.get("mock_delay", 0.05))
        secs = len(pcm16) / 2 / sample_rate
        text = options.get("mock_text") or f"Hello, this is a {secs:.1f} second test utterance."
        return STTResult(text=text, language=language, confidence=1.0)


@register
class MockLLM(LLMProvider):
    meta = ProviderMeta("llm", "mock", "Mock LLM (offline demo)", [], features=["no-key"])

    async def stream(self, messages, *, model, temperature, max_tokens, options):
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        reply = options.get("mock_reply") or (
            f"You said: {last}. This is the mock assistant speaking. "
            f"Temperature is {temperature:.1f}. Try interrupting me while I am talking to test barge-in.")
        for w in reply.split(" "):
            await asyncio.sleep(options.get("mock_token_delay", 0.01))
            yield w + " "


@register
class MockTTS(TTSProvider):
    meta = ProviderMeta("tts", "mock", "Mock TTS - placeholder TONES, not speech (offline tests)", [],
                        features=["no-key", "offline", "tones-only"],
                        notes="Plays a beep-like tone per sentence so the pipeline can be tested fully offline. "
                              "Use 'Free voices' or a real provider to hear actual speech.")

    async def synthesize(self, text, language, voice, opts, model=None):
        sr = 16000
        dur = min(4.0, max(0.3, 0.06 * len(text) / max(opts.speed, 0.1)))
        t = np.arange(int(sr * dur)) / sr
        f = 220 * (2 ** (opts.pitch / 12))
        env = np.minimum(1, np.minimum(t / 0.02, (dur - t) / 0.02))
        sig = 0.2 * np.sin(2 * np.pi * f * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t)) * env
        pcm = (sig * 32767).astype("<i2").tobytes()
        await asyncio.sleep(0.02)
        return AudioChunk(pcm_to_wav(pcm, sr), "wav", sr)
