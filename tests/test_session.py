import asyncio

import pytest

from app.models import BotProfile
from app.pipeline.session import CallSession
from app.providers import Credentials
from tests.conftest import frames, silence, voice


def make(wire, **over):
    p = BotProfile(language="hi-IN", **over)
    p.vad.end_silence_ms = over.get("end_silence_ms", 500)
    p.audio.noise_reduction = True
    return CallSession(p, Credentials(), wire.send_json, wire.send_bytes)


async def feed(s, pcm, step=0.002):
    for f in frames(pcm):
        await s.on_audio(f)
        await asyncio.sleep(step)


async def wait_for(cond, timeout=5.0):
    t = 0
    while not cond():
        await asyncio.sleep(0.02)
        t += 0.02
        if t > timeout:
            raise AssertionError("timeout")


async def test_full_turn_with_mock_providers(wire):
    s = make(wire)
    await s.start()
    await feed(s, silence(0.5) + voice(1.0) + silence(0.8))
    await wait_for(lambda: "metrics" in wire.types() and wire.of("assistant_done"))
    t = wire.types()
    assert t[0] == "ready"
    assert "speech_start" in [m.get("state") for m in wire.of("vad")]
    assert wire.of("transcript")[0]["text"].startswith("Hello")
    assert wire.of("llm_delta")
    assert wire.of("audio") and len(wire.bytes) == len(wire.of("audio"))
    m = wire.of("metrics")[-1]
    for k in ("stt_ms", "llm_ttft_ms", "llm_total_ms", "tts_first_ms", "e2e_first_audio_ms"):
        assert k in m and m[k] >= 0
    await wait_for(lambda: wire.of("sentiment"))
    assert s.history[-1]["role"] == "assistant"
    await s.close()


async def test_barge_in_cancels_response(wire):
    s = make(wire, llm={"provider": "mock", "options": {"mock_token_delay": 0.05}})
    await s.start()
    await feed(s, silence(0.5) + voice(1.0) + silence(0.7))
    await wait_for(lambda: wire.of("audio"))  # bot started talking
    assert s.bot_busy
    await feed(s, voice(0.6))  # user talks over the bot
    await wait_for(lambda: wire.of("interrupt"))
    ev = wire.of("interrupt")[0]
    assert ev["reason"] == "barge_in"
    assert s.response_task.done() and not s.bot_speaking
    done = [m for m in wire.of("assistant_done") if m.get("interrupted")]
    assert done, "partial response should be marked interrupted"
    assert "[interrupted" in s.history[-1]["content"]
    n_audio = len(wire.of("audio"))
    await asyncio.sleep(0.3)
    assert len(wire.of("audio")) == n_audio  # no audio after interruption
    await s.close()


async def test_short_noise_does_not_barge_in(wire):
    s = make(wire, llm={"provider": "mock", "options": {"mock_token_delay": 0.05}})
    await s.start()
    await feed(s, silence(0.5) + voice(1.0) + silence(0.7))
    await wait_for(lambda: wire.of("audio"))
    await feed(s, voice(0.15) + silence(0.3))  # cough / click
    assert not wire.of("interrupt")
    await s.close()


async def test_barge_in_disabled_half_duplex(wire):
    s = make(wire, llm={"provider": "mock", "options": {"mock_token_delay": 0.05}})
    s.p.vad.barge_in = False
    await s.start()
    await feed(s, silence(0.5) + voice(1.0) + silence(0.7))
    await wait_for(lambda: wire.of("audio"))
    await feed(s, voice(0.8) + silence(0.7))
    assert not wire.of("interrupt")
    assert len(wire.of("transcript")) == 1  # second utterance ignored while bot busy
    await s.close()


async def test_speaker_lock_ignores_other_voice_for_barge_in(wire):
    s = make(wire, llm={"provider": "mock", "options": {"mock_token_delay": 0.05}},
             speaker={"enabled": True, "threshold": 0.0, "enroll_seconds": 1.0})
    await s.start()
    me = voice(1.4, f0=110, formants=(600, 1100, 2500), seed=1)
    await feed(s, silence(0.5) + me + silence(0.7))
    await wait_for(lambda: wire.of("audio"))
    assert s.speaker.enrolled
    # calibrate threshold between my voice and a very different voice
    _, sim_me = s.speaker.check_only(voice(1.0, f0=112, formants=(610, 1090, 2480), seed=7))
    _, sim_other = s.speaker.check_only(voice(1.0, f0=250, formants=(950, 2100, 3300), seed=8))
    assert sim_me > sim_other
    s.speaker.threshold = (sim_me + sim_other) / 2
    await feed(s, voice(0.8, f0=250, formants=(950, 2100, 3300), seed=9))
    assert not wire.of("interrupt")
    assert any(m.get("event") == "barge_in_ignored" for m in wire.of("speaker"))
    await s.close()


async def test_text_input_and_playback_end(wire):
    s = make(wire)
    await s.start()
    await s.on_text("मुझे बहुत गुस्सा आ रहा है")
    await wait_for(lambda: wire.of("metrics"))
    await wait_for(lambda: wire.of("sentiment"))
    assert wire.of("sentiment")[0]["label"] == "negative"
    assert s.bot_speaking
    await s.on_playback_ended(s.turn_id)
    assert not s.bot_speaking
    await s.close()


async def test_typed_message_interrupts(wire):
    s = make(wire, llm={"provider": "mock", "options": {"mock_token_delay": 0.05}})
    await s.start()
    await s.on_text("first")
    await wait_for(lambda: wire.of("llm_delta"))
    await s.on_text("second")
    assert wire.of("interrupt")
    await wait_for(lambda: len([m for m in wire.of("assistant_done") if not m.get("interrupted")]) >= 1, 10)
    await s.close()


async def test_provider_error_reported(wire):
    s = make(wire, llm={"provider": "openai"})  # no key configured
    s.creds.allow_env = False
    await s.start()
    await s.on_text("hello")
    await wait_for(lambda: wire.of("error"))
    assert "missing" in wire.of("error")[0]["message"].lower()
    await s.close()


async def test_greeting_spoken(wire):
    s = make(wire, llm={"provider": "mock", "greeting": "नमस्ते! मैं आपकी कैसे मदद कर सकती हूँ?"})
    await s.start()
    await wait_for(lambda: wire.of("audio"))
    assert s.history[0]["role"] == "assistant"
    await s.close()


async def test_unsupported_language_warning(wire):
    s = make(wire, tts={"provider": "aws"})
    s.p.language = "ta-IN"
    await s.start()
    assert wire.of("ready")[0]["warnings"]
