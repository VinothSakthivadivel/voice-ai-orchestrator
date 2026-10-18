"""Verify request shapes of real provider integrations using httpx.MockTransport (no network)."""
import base64
import json

import httpx
import pytest

from app.providers import Credentials, TTSOptions, get_provider
from app.providers.base import ProviderError, pcm_to_wav
from app.providers.tts import azure_ssml

PCM = b"\x00\x00" * 1600


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


async def test_sarvam_stt():
    seen = {}

    def h(req):
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers["api-subscription-key"], req.content
        return httpx.Response(200, json={"transcript": "नमस्ते", "language_code": "hi-IN"})

    p = get_provider("stt", "sarvam", Credentials({"sarvam": {"api_key": "sv-key"}}), client(h))
    r = await p.transcribe(PCM, 16000, "or-IN", {})
    assert r.text == "नमस्ते" and seen["key"] == "sv-key"
    assert b"od-IN" in seen["body"] and b"saaras:v4" in seen["body"]


async def test_deepgram_diarization_words():
    def h(req):
        assert req.url.params["language"] == "hi" and req.url.params["diarize"] == "true"
        assert req.headers["authorization"] == "Token dg"
        return httpx.Response(200, json={"results": {"channels": [{"alternatives": [{
            "transcript": "haan ji", "confidence": 0.9,
            "words": [{"word": "haan", "punctuated_word": "Haan", "speaker": 0}, {"word": "ji", "speaker": 1}]}]}]}})

    p = get_provider("stt", "deepgram", Credentials({"deepgram": {"api_key": "dg"}}), client(h))
    r = await p.transcribe(PCM, 16000, "hi-IN", {"diarization": True})
    assert r.text == "haan ji" and r.words[1]["speaker"] == 1


async def test_google_stt_body():
    def h(req):
        assert req.url.params["key"] == "gk"
        b = json.loads(req.content)
        assert b["config"]["languageCode"] == "pa-Guru-IN" and b["config"]["sampleRateHertz"] == 16000
        assert base64.b64decode(b["audio"]["content"]) == PCM
        return httpx.Response(200, json={"results": [{"alternatives": [{"transcript": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ", "confidence": 0.8}]}]})

    p = get_provider("stt", "google", Credentials({"google_cloud": {"api_key": "gk"}}), client(h))
    assert (await p.transcribe(PCM, 16000, "pa-IN", {})).text == "ਸਤ ਸ੍ਰੀ ਅਕਾਲ"


async def test_azure_stt():
    def h(req):
        assert req.url.host == "centralindia.stt.speech.microsoft.com"
        assert req.url.params["language"] == "ta-IN"
        return httpx.Response(200, json={"RecognitionStatus": "Success", "NBest": [{"Display": "வணக்கம்", "Confidence": 0.9}]})

    p = get_provider("stt", "azure", Credentials({"azure_speech": {"key": "az"}}), client(h))
    assert (await p.transcribe(PCM, 16000, "ta-IN", {})).text == "வணக்கம்"


async def test_openai_stt_and_unsupported_language():
    def h(req):
        assert req.url.path == "/v1/audio/transcriptions" and b'name="language"' in req.content
        return httpx.Response(200, json={"text": "hello"})

    p = get_provider("stt", "openai", Credentials({"openai": {"api_key": "sk-x"}}), client(h))
    assert (await p.transcribe(PCM, 16000, "ta-IN", {})).text == "hello"
    with pytest.raises(ProviderError):
        await p.transcribe(PCM, 16000, "or-IN", {})


async def test_openai_llm_streaming():
    def h(req):
        b = json.loads(req.content)
        assert b["stream"] and b["temperature"] == 0.3 and b["max_tokens"] == 50
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "Nam"}}]},
                                            {"choices": [{"delta": {"content": "aste"}}]}))

    p = get_provider("llm", "openai", Credentials({"openai": {"api_key": "sk-x"}}), client(h))
    out = [t async for t in p.stream([{"role": "user", "content": "hi"}], model=None, temperature=0.3,
                                     max_tokens=50, options={})]
    assert "".join(out) == "Namaste"


async def test_groq_uses_its_base_url():
    def h(req):
        assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "ok"}}]}))

    p = get_provider("llm", "groq", Credentials({"groq": {"api_key": "gsk"}}), client(h))
    assert await p.complete([{"role": "user", "content": "x"}], model=None, temperature=0.1, max_tokens=5, options={}) == "ok"


async def test_azure_openai_url():
    def h(req):
        assert req.url.path == "/openai/deployments/gpt4o/chat/completions"
        assert req.headers["api-key"] == "ak"
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "hi"}}]}))

    c = Credentials({"azure_openai": {"api_key": "ak", "endpoint": "https://x.openai.azure.com", "deployment": "gpt4o"}})
    p = get_provider("llm", "azure_openai", c, client(h))
    assert await p.complete([{"role": "user", "content": "x"}], model=None, temperature=0.1, max_tokens=5, options={}) == "hi"


async def test_anthropic_streaming_and_system():
    def h(req):
        b = json.loads(req.content)
        assert b["system"] == "be brief" and b["messages"][0]["role"] == "user"
        assert "temperature" not in b  # claude-haiku-5-5 rejects sampling params
        ev = [{"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}},
              {"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "hmm"}},
              {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}}]
        return httpx.Response(200, text="".join(f"event: x\ndata: {json.dumps(e)}\n\n" for e in ev))

    p = get_provider("llm", "anthropic", Credentials({"anthropic": {"api_key": "a"}}), client(h))
    msgs = [{"role": "system", "content": "be brief"}, {"role": "assistant", "content": "greeting"},
            {"role": "user", "content": "hi"}]
    assert await p.complete(msgs, model=None, temperature=0.2, max_tokens=10, options={}) == "Hello"


async def test_gemini_streaming():
    def h(req):
        b = json.loads(req.content)
        assert b["generationConfig"]["temperature"] == 0.7 and "systemInstruction" in b
        assert b["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "low"
        return httpx.Response(200, text=sse({"candidates": [{"content": {"parts": [{"text": "नमस्ते"}]}}]}))

    p = get_provider("llm", "gemini", Credentials({"gemini": {"api_key": "g"}}), client(h))
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    assert await p.complete(msgs, model=None, temperature=0.7, max_tokens=10, options={}) == "नमस्ते"


async def test_error_body_redacts_key():
    def h(req):
        return httpx.Response(401, text="invalid key sv-supersecret-123")

    p = get_provider("tts", "sarvam", Credentials({"sarvam": {"api_key": "sv-supersecret-123"}}), client(h))
    with pytest.raises(ProviderError) as e:
        await p.synthesize("hi", "hi-IN", None, TTSOptions())
    assert "supersecret" not in str(e.value) and "401" in str(e.value)


async def test_sarvam_tts():
    wav = pcm_to_wav(PCM, 22050)

    def h(req):
        b = json.loads(req.content)
        assert b["language_code"] == "ta-IN" and b["speaker"] == "shubh" and b["pace"] == 1.2
        assert b["model"] == "bulbul:v3" and "pitch" not in b  # pitch/loudness are bulbul:v2-only
        return httpx.Response(200, json={"audios": [base64.b64encode(wav).decode()]})

    p = get_provider("tts", "sarvam", Credentials({"sarvam": {"api_key": "k"}}), client(h))
    ch = await p.synthesize("வணக்கம்", "ta-IN", None, TTSOptions(speed=1.2))
    assert ch.format == "wav" and ch.data == wav


async def test_azure_tts_ssml_style():
    ssml = azure_ssml("नमस्ते & स्वागत", "hi-IN", "hi-IN-SwaraNeural", TTSOptions(speed=1.2, pitch=2, style="cheerful"))
    assert 'style="cheerful"' in ssml and 'rate="+20%"' in ssml and 'pitch="+2st"' in ssml and "&amp;" in ssml

    def h(req):
        assert req.headers["x-microsoft-outputformat"].startswith("riff-24khz")
        assert b"hi-IN-SwaraNeural" in req.content
        return httpx.Response(200, content=pcm_to_wav(PCM, 24000))

    p = get_provider("tts", "azure", Credentials({"azure_speech": {"key": "k", "region": "eastus"}}), client(h))
    ch = await p.synthesize("नमस्ते", "hi-IN", None, TTSOptions())
    assert ch.format == "wav"


async def test_elevenlabs_tts():
    def h(req):
        b = json.loads(req.content)
        assert req.url.params["output_format"] == "pcm_16000"
        assert b["voice_settings"]["stability"] == 0.3 and b["language_code"] == "hi"
        return httpx.Response(200, content=PCM)

    p = get_provider("tts", "elevenlabs", Credentials({"elevenlabs": {"api_key": "k"}}), client(h))
    ch = await p.synthesize("hi", "hi-IN", None, TTSOptions(stability=0.3))
    assert ch.format == "pcm16" and ch.sample_rate == 16000


async def test_openai_tts_emotion_instructions():
    def h(req):
        b = json.loads(req.content)
        assert b["instructions"] and b["speed"] == 1.5 and b["response_format"] == "wav"
        return httpx.Response(200, content=pcm_to_wav(PCM, 24000))

    p = get_provider("tts", "openai", Credentials({"openai": {"api_key": "k"}}), client(h))
    await p.synthesize("hello", "en-IN", "coral", TTSOptions(speed=1.5, style="empathetic"))


async def test_google_tts():
    def h(req):
        b = json.loads(req.content)
        assert b["voice"]["languageCode"] == "bn-IN" and b["audioConfig"]["audioEncoding"] == "LINEAR16"
        return httpx.Response(200, json={"audioContent": base64.b64encode(pcm_to_wav(PCM, 24000)).decode()})

    p = get_provider("tts", "google", Credentials({"google_cloud": {"api_key": "k"}}), client(h))
    assert (await p.synthesize("নমস্কার", "bn-IN", None, TTSOptions())).format == "wav"


async def test_sarvam_llm_and_deepgram_indic():
    def h(req):
        b = json.loads(req.content)
        assert b["model"] == "sarvam-105b-conversations" and b["reasoning_effort"] == "low"
        assert req.headers["api-subscription-key"] == "k"
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "नमस्ते"}}]}))

    p = get_provider("llm", "sarvam", Credentials({"sarvam": {"api_key": "k"}}), client(h))
    assert await p.complete([{"role": "user", "content": "hi"}], model=None, temperature=0.2, max_tokens=9, options={}) == "नमस्ते"
