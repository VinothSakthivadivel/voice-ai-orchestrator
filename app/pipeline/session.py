"""Realtime call session: VAD -> turn detection -> (denoise, speaker gate) -> STT -> LLM stream
-> sentence chunker -> TTS -> client, with barge-in / interruption handling.

Transport-agnostic: the WebSocket route passes ``send_json`` / ``send_bytes`` coroutines.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from ..analysis.sentiment import analyze_lexicon, analyze_llm
from ..audio.denoise import NoiseReducer
from ..audio.speaker import SpeakerGate, filter_words_by_speaker
from ..audio.vad import EnergyVAD, TurnDetector
from ..languages import supports
from ..models import BotProfile
from ..providers import Credentials, ProviderError, TTSOptions, get_provider
from ..providers.base import wav_info
from .chunker import SentenceChunker

log = logging.getLogger("voice.session")

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * 2 * FRAME_MS // 1000  # 640


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


class CallSession:
    def __init__(self, profile: BotProfile, creds: Credentials,
                 send_json: Callable[[dict], Awaitable[None]], send_bytes: Callable[[bytes], Awaitable[None]]):
        self.p = profile
        self.creds = creds
        self._send_json = send_json
        self._send_bytes = send_bytes
        self._lock = asyncio.Lock()
        self.stt = get_provider("stt", profile.stt.provider, creds)
        self.llm = get_provider("llm", profile.llm.provider, creds)
        self.tts = get_provider("tts", profile.tts.provider, creds)
        v = profile.vad
        self.vad = EnergyVAD(SAMPLE_RATE, v.margin_db, v.min_db, v.mode)
        self.turns = TurnDetector(FRAME_MS, v.min_speech_ms, v.end_silence_ms, 300, v.max_utterance_ms)
        self.denoiser = NoiseReducer(SAMPLE_RATE, profile.audio.noise_strength)
        sp = profile.speaker
        self.speaker = SpeakerGate(sp.enabled, sp.threshold, sp.enroll_seconds)
        self.primary_diar_speaker = None
        self.history: list[dict] = []
        self.response_task: asyncio.Task | None = None
        self._speak_until = 0.0
        self.bot_speaking = False      # audio sent and (maybe) still playing on client
        self.turn_id = 0
        self._pending = b""
        self._barge_checked = False
        self._level_t = 0.0
        self._bg: set[asyncio.Task] = set()
        self.closed = False
        self.stats = {"turns": 0, "interruptions": 0, "rejected_speaker": 0}

    # ------------------------------------------------------------------ transport
    async def send(self, msg: dict):
        if self.closed:
            return
        async with self._lock:
            await self._send_json(msg)

    async def send_audio(self, meta: dict, data: bytes):
        if self.closed:
            return
        async with self._lock:  # keep meta + binary adjacent on the wire
            await self._send_json(meta)
            await self._send_bytes(data)

    def _spawn(self, coro):
        t = asyncio.create_task(coro)
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)
        return t

    @property
    def bot_busy(self) -> bool:
        if self.bot_speaking and time.monotonic() > self._speak_until + 2.0:
            self.bot_speaking = False  # safety net if the client never reports playback end
        return self.bot_speaking or (self.response_task is not None and not self.response_task.done())

    def _extend_speaking(self, chunk):
        if chunk.format == "pcm16":
            dur = len(chunk.data) / 2 / chunk.sample_rate
        elif chunk.format == "wav":
            info = wav_info(chunk.data)
            dur = len(info[1]) / 2 / info[0] if info else len(chunk.data) / 32000
        else:  # mp3 ~ 128 kbps
            dur = len(chunk.data) / 16000
        now = time.monotonic()
        self._speak_until = max(self._speak_until, now) + dur
        self.bot_speaking = True

    # ------------------------------------------------------------------ lifecycle
    async def start(self):
        warn = []
        lang = self.p.language
        if lang != "auto":
            if not supports("stt", self.p.stt.provider, lang):
                warn.append(f"STT '{self.p.stt.provider}' has no {lang} support")
            if not supports("tts", self.p.tts.provider, lang):
                warn.append(f"TTS '{self.p.tts.provider}' has no {lang} support")
        await self.send({"type": "ready", "vad_backend": self.vad.backend, "denoise_backend": self.denoiser.backend,
                         "speaker_embedder": self.speaker.embedder.name, "warnings": warn,
                         "profile": {"name": self.p.name, "language": lang, "stt": self.p.stt.provider,
                                     "llm": self.p.llm.provider, "tts": self.p.tts.provider}})
        if self.p.llm.greeting:
            self.history.append({"role": "assistant", "content": self.p.llm.greeting})
            await self.send({"type": "assistant_done", "text": self.p.llm.greeting, "turn": 0})
            self.response_task = asyncio.create_task(self._speak_only(self.p.llm.greeting))

    async def close(self):
        self.closed = True
        await self._cancel_response()
        for t in list(self._bg):
            t.cancel()

    # ------------------------------------------------------------------ inputs
    async def on_audio(self, data: bytes):
        self._pending += data
        while len(self._pending) >= FRAME_BYTES:
            frame, self._pending = self._pending[:FRAME_BYTES], self._pending[FRAME_BYTES:]
            await self._on_frame(frame)

    async def on_text(self, text: str):
        text = text.strip()
        if not text:
            return
        if self.bot_busy:
            await self.interrupt("typed")
        await self.send({"type": "transcript", "role": "user", "text": text, "final": True, "source": "text"})
        self._spawn(self._sentiment(text, self.turn_id + 1))
        await self.respond(text)

    async def on_playback_ended(self, turn: int | None = None):
        if turn is None or turn == self.turn_id:
            if self.response_task is None or self.response_task.done():
                self.bot_speaking = False
                await self.send({"type": "state", "state": "listening"})

    async def enroll_speaker(self, reset: bool = True):
        if reset:
            self.speaker.reset_enrollment()
        self.speaker.enabled = True
        await self.send({"type": "speaker", "event": "enrolling", "seconds": self.speaker.enroll_seconds})

    # ------------------------------------------------------------------ frame processing
    async def _on_frame(self, frame: bytes):
        speech = self.vad.is_speech(frame)
        if not speech and not self.turns.in_speech:
            self.denoiser.add_noise_frame(frame)
        now = time.monotonic()
        if now - self._level_t > 0.1:
            self._level_t = now
            await self.send({"type": "level", "db": round(self.vad.last_db, 1),
                             "floor": round(self.vad.noise_floor, 1), "speech": speech})
        for ev in self.turns.feed(frame, speech):
            if ev.kind == "speech_start":
                self._barge_checked = False
                await self.send({"type": "vad", "state": "speech_start", "bot_busy": self.bot_busy})
            elif ev.kind == "speech_end":
                await self.send({"type": "vad", "state": "speech_end", "speech_ms": ev.speech_ms})
                self._spawn(self._handle_utterance(ev.audio, ev.speech_ms))
        # barge-in: sustained speech while bot is busy
        if (self.turns.in_speech and not self._barge_checked and self.bot_busy and self.p.vad.barge_in):
            need = self.p.vad.barge_in_ms
            if self.speaker.enabled and self.speaker.enrolled:
                need = max(need, 500)  # need a bit of audio for a voiceprint
            if self.turns.speech_ms >= need:
                self._barge_checked = True
                ok, sim = self.speaker.check_only(self.turns.current_audio) if self.speaker.enabled else (True, None)
                if ok:
                    await self.interrupt("barge_in", similarity=sim)
                else:
                    await self.send({"type": "speaker", "event": "barge_in_ignored", "similarity": sim})

    async def interrupt(self, reason: str, **extra):
        if not self.bot_busy:
            return
        self.stats["interruptions"] += 1
        await self._cancel_response()
        self.bot_speaking = False
        await self.send({"type": "interrupt", "reason": reason, "turn": self.turn_id, **extra})

    async def _cancel_response(self):
        t = self.response_task
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------------ utterance -> response
    async def _handle_utterance(self, audio: bytes, speech_ms: int):
        t_end = time.perf_counter()
        if self.bot_busy and not self.p.vad.barge_in:
            return  # half-duplex mode: ignore speech while the bot talks
        if speech_ms < self.p.vad.min_speech_ms:
            return
        if self.speaker.enabled:
            ok, sim = self.speaker.check(audio)
            await self.send({"type": "speaker", "event": "accepted" if ok else "rejected",
                             "similarity": None if sim is None else round(sim, 3), "enrolled": self.speaker.enrolled})
            if not ok:
                self.stats["rejected_speaker"] += 1
                return
        if self.p.audio.noise_reduction:
            try:
                audio = await asyncio.to_thread(self.denoiser.reduce, audio)
            except Exception:  # never fail a turn on denoise
                log.exception("denoise failed")
        t0 = time.perf_counter()
        try:
            opts = {**self.p.stt.options, "model": self.p.stt.model,
                    "diarization": self.p.stt.diarization or self.p.speaker.use_provider_diarization}
            res = await self.stt.transcribe(audio, SAMPLE_RATE, self.p.language, opts)
        except ProviderError as e:
            await self.send({"type": "error", "layer": "stt", "message": str(e)})
            return
        except Exception as e:  # noqa: BLE001
            await self.send({"type": "error", "layer": "stt", "message": f"{type(e).__name__}: {e}"})
            return
        stt_ms = _ms(t0)
        text = res.text.strip()
        if self.p.speaker.use_provider_diarization and res.words:
            text, self.primary_diar_speaker = filter_words_by_speaker(res.words, self.primary_diar_speaker)
        if not text:
            await self.send({"type": "metrics", "stt_ms": stt_ms, "empty": True})
            return
        if self.bot_busy:  # user spoke over the bot (barge-in not yet triggered, e.g. short phrase)
            await self.interrupt("new_utterance")
        await self.send({"type": "transcript", "role": "user", "text": text, "final": True,
                         "language": res.language, "confidence": res.confidence})
        self._spawn(self._sentiment(text, self.turn_id + 1))
        await self.respond(text, stt_ms=stt_ms, t_speech_end=t_end)

    async def _sentiment(self, text: str, turn: int):
        mode = self.p.sentiment.mode
        if mode == "off":
            return
        s = (await analyze_llm(text, self.llm, self.p.llm.model)) if mode == "llm" else analyze_lexicon(text)
        await self.send({"type": "sentiment", "turn": turn, "text": text[:120], **s.to_dict()})

    async def respond(self, text: str, stt_ms: int | None = None, t_speech_end: float | None = None):
        await self._cancel_response()
        self.history.append({"role": "user", "content": text})
        self.turn_id += 1
        self.stats["turns"] += 1
        self.response_task = asyncio.create_task(
            self._respond(self.turn_id, stt_ms, t_speech_end or time.perf_counter()))

    def _messages(self) -> list[dict]:
        n = self.p.llm.history_turns * 2
        hist = self.history[-n:] if n else self.history[-1:]
        sys = self.p.llm.system_prompt
        if self.p.language not in ("auto", "en-IN"):
            sys += f"\nPreferred language: {self.p.language}."
        return [{"role": "system", "content": sys}, *hist]

    def _tts_opts(self) -> TTSOptions:
        t = self.p.tts
        return TTSOptions(speed=t.speed, pitch=t.pitch, style=t.style, style_degree=t.style_degree,
                          stability=t.stability, similarity=t.similarity, style_exaggeration=t.style_exaggeration,
                          volume=t.volume)

    async def _tts_worker(self, turn: int, q: asyncio.Queue, metrics: dict, t_ref: float):
        seq = 0
        lang = self.p.language if self.p.language != "auto" else "en-IN"
        while True:
            sent = await q.get()
            if sent is None:
                return
            t0 = time.perf_counter()
            chunk = await self.tts.synthesize(sent, lang, self.p.tts.voice, self._tts_opts(), self.p.tts.model)
            dt = _ms(t0)
            if seq == 0:
                metrics["tts_first_ms"] = dt
                metrics["e2e_first_audio_ms"] = _ms(t_ref)
            metrics["tts_total_ms"] = metrics.get("tts_total_ms", 0) + dt
            self._extend_speaking(chunk)
            await self.send_audio({"type": "audio", "turn": turn, "seq": seq, "format": chunk.format,
                                   "sample_rate": chunk.sample_rate, "text": sent}, chunk.data)
            seq += 1

    async def _respond(self, turn: int, stt_ms: int | None, t_ref: float):
        metrics: dict = {"turn": turn}
        if stt_ms is not None:
            metrics["stt_ms"] = stt_ms
        q: asyncio.Queue = asyncio.Queue()
        tts_task = asyncio.create_task(self._tts_worker(turn, q, metrics, t_ref))
        chunker = SentenceChunker()
        reply: list[str] = []
        t0 = time.perf_counter()
        await self.send({"type": "state", "state": "thinking", "turn": turn})
        try:
            first = True
            async for tok in self.llm.stream(self._messages(), model=self.p.llm.model,
                                             temperature=self.p.llm.temperature, max_tokens=self.p.llm.max_tokens,
                                             options={**self.p.llm.options, "top_p": self.p.llm.top_p}):
                if first:
                    metrics["llm_ttft_ms"] = _ms(t0)
                    first = False
                reply.append(tok)
                await self.send({"type": "llm_delta", "turn": turn, "text": tok})
                for s in chunker.feed(tok):
                    q.put_nowait(s)
                if tts_task.done():
                    break
            metrics["llm_total_ms"] = _ms(t0)
            for s in chunker.flush():
                q.put_nowait(s)
            q.put_nowait(None)
            await tts_task
            text = "".join(reply).strip()
            self.history.append({"role": "assistant", "content": text})
            await self.send({"type": "assistant_done", "turn": turn, "text": text})
            metrics["total_ms"] = _ms(t_ref)
            await self.send({"type": "metrics", **metrics})
            if not self.bot_speaking:
                await self.send({"type": "state", "state": "listening"})
        except asyncio.CancelledError:
            tts_task.cancel()
            partial = "".join(reply).strip()
            if partial:
                self.history.append({"role": "assistant", "content": partial + " …[interrupted by user]"})
            await self.send({"type": "assistant_done", "turn": turn, "text": partial, "interrupted": True})
            raise
        except Exception as e:  # noqa: BLE001
            tts_task.cancel()
            layer = "tts" if tts_task.done() and not tts_task.cancelled() and tts_task.exception() else "llm"
            await self.send({"type": "error", "layer": layer, "message": str(e) or type(e).__name__})
            await self.send({"type": "state", "state": "listening"})

    async def _speak_only(self, text: str):
        q: asyncio.Queue = asyncio.Queue()
        for s in SentenceChunker().feed(text + " ") or [text]:
            q.put_nowait(s)
        q.put_nowait(None)
        try:
            await self._tts_worker(self.turn_id, q, {}, time.perf_counter())
        except Exception as e:  # noqa: BLE001
            await self.send({"type": "error", "layer": "tts", "message": str(e)})
