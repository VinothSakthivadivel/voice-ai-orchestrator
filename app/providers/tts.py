"""Text-to-speech providers. Each sentence is synthesised and streamed to the client."""
from __future__ import annotations

import asyncio
import base64
from xml.sax.saxutils import escape  # noqa: E402

from ..languages import ELEVENLABS_DEFAULT_VOICE, default_voice, provider_code
from . import creds as C
from .base import AudioChunk, ProviderError, ProviderMeta, TTSOptions, TTSProvider, register

AZURE_STYLES = ["cheerful", "empathetic", "calm", "sad", "angry", "excited", "friendly", "hopeful",
                "serious", "customerservice", "assistant", "chat", "newscast", "whispering", "shouting"]
EMOTION_INSTRUCTIONS = {
    "cheerful": "Speak in a warm, cheerful and upbeat tone.",
    "empathetic": "Speak softly with empathy and reassurance, like a caring support agent.",
    "calm": "Speak calmly and slowly with a soothing tone.",
    "excited": "Speak with high energy and excitement.",
    "serious": "Speak in a serious, professional tone.",
    "sad": "Speak in a gentle, sad tone.",
    "friendly": "Speak in a friendly, conversational tone.",
    "customerservice": "Speak like a polite, efficient customer service agent.",
}


def _lang(name: str, language: str) -> str:
    code = provider_code("tts", name, language)
    if code is None or code == "auto":
        raise ProviderError(f"TTS provider '{name}' does not support language {language}")
    return code


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


@register
class SarvamTTS(TTSProvider):
    meta = ProviderMeta("tts", "sarvam", "Sarvam AI (Bulbul)", C.SARVAM,
                        models=["bulbul:v3", "bulbul:v4-flash", "bulbul:v2"], default_model="bulbul:v3",
                        features=["speed", "pitch+loudness(v2 only)", "indic-voices", "200+ personas (v4-flash)"])

    async def synthesize(self, text, language, voice, opts: TTSOptions, model=None):
        m = model or self.meta.default_model
        body = {"text": text, "language_code": _lang("sarvam", language), "model": m,
                "speech_sample_rate": 22050}
        if voice or m != "bulbul:v4-flash":
            body["speaker"] = voice or ("anushka" if m == "bulbul:v2" else default_voice("sarvam", language) or "shubh")
        if m == "bulbul:v2":
            body.update({"pace": _clamp(opts.speed, 0.3, 3.0), "pitch": _clamp(opts.pitch * 0.075, -0.75, 0.75),
                         "loudness": _clamp(opts.volume, 0.3, 3.0), "enable_preprocessing": True})
        else:
            body["pace"] = _clamp(opts.speed, 0.5, 2.0)
        r = await self.http.post("https://api.sarvam.ai/text-to-speech", json=body,
                                 headers={"api-subscription-key": self.cred("api_key")})
        await self._check(r)
        audios = r.json().get("audios") or []
        if not audios:
            raise ProviderError("Sarvam TTS returned no audio")
        if len(audios) == 1:
            return AudioChunk(base64.b64decode(audios[0]), "wav", 22050)
        # several base64 WAVs: decode each separately (joining base64 strings corrupts the audio)
        from ..audio.codec import parse_wav
        pcm, rate = b"", 22050
        for a in audios:
            p, rate = parse_wav(base64.b64decode(a))
            pcm += p
        return AudioChunk(pcm, "pcm16", rate)


@register
class GoogleTTS(TTSProvider):
    meta = ProviderMeta("tts", "google", "Google Cloud Text-to-Speech", C.GOOGLE_CLOUD, cred_group="google_cloud",
                        features=["speed", "pitch"])

    async def synthesize(self, text, language, voice, opts, model=None):
        lang = _lang("google", language)
        v = {"languageCode": lang}
        if voice:
            v["name"] = voice
        body = {"input": {"text": text}, "voice": v,
                "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000,
                                "speakingRate": _clamp(opts.speed, 0.25, 4.0),
                                "pitch": _clamp(opts.pitch * 2, -20, 20)}}
        r = await self.http.post("https://texttospeech.googleapis.com/v1/text:synthesize",
                                 params={"key": self.cred("api_key")}, json=body)
        await self._check(r)
        return AudioChunk(base64.b64decode(r.json()["audioContent"]), "wav", 24000)


def azure_ssml(text: str, lang: str, voice: str, opts: TTSOptions) -> str:
    rate = f"{(opts.speed - 1) * 100:+.0f}%"
    pitch = f"{opts.pitch:+.0f}st"
    inner = f'<prosody rate="{rate}" pitch="{pitch}">{escape(text)}</prosody>'
    if opts.style and opts.style != "neutral":
        inner = (f'<mstts:express-as style="{escape(opts.style)}" styledegree="{_clamp(opts.style_degree, 0.01, 2):.2f}">'
                 f"{inner}</mstts:express-as>")
    return ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
            f'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{lang}">'
            f'<voice name="{escape(voice)}">{inner}</voice></speak>')


@register
class AzureTTS(TTSProvider):
    meta = ProviderMeta("tts", "azure", "Azure AI Speech (Neural TTS)", C.AZURE_SPEECH, cred_group="azure_speech",
                        features=["speed", "pitch", "emotion-styles(SSML express-as)"],
                        notes="express-as styles only apply to voices that support them; others ignore the style.")

    async def synthesize(self, text, language, voice, opts, model=None):
        lang = _lang("azure", language)
        if not voice or (voice.count("-") >= 2 and not voice.startswith(lang)):  # voice from another language
            voice = default_voice("azure", language)
        url = f"https://{self.cred('region')}.tts.speech.microsoft.com/cognitiveservices/v1"
        r = await self.http.post(url, content=azure_ssml(text, lang, voice, opts).encode("utf-8"),
                                 headers={"Ocp-Apim-Subscription-Key": self.cred("key"),
                                          "Content-Type": "application/ssml+xml",
                                          "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
                                          "User-Agent": "voice-ai-orchestrator"})
        await self._check(r)
        return AudioChunk(r.content, "wav", 24000)


@register
class PollyTTS(TTSProvider):
    meta = ProviderMeta("tts", "aws", "AWS Polly", C.AWS, cred_group="aws", models=["neural", "standard"],
                        default_model="neural", features=["speed(SSML)"])

    async def synthesize(self, text, language, voice, opts, model=None):
        import boto3

        lang = _lang("aws", language)
        voice = voice or default_voice("aws", language) or "Kajal"
        engine = model or self.meta.default_model
        if voice in ("Aditi", "Raveena"):
            engine = "standard"
        ssml = f'<speak><prosody rate="{int(_clamp(opts.speed, 0.2, 2.0) * 100)}%">{escape(text)}</prosody></speak>'

        def run():
            c = boto3.client("polly", region_name=self.cred("region"), aws_access_key_id=self.cred("access_key_id"),
                             aws_secret_access_key=self.cred("secret_access_key"),
                             aws_session_token=self.opt_cred("session_token"))
            r = c.synthesize_speech(Text=ssml, TextType="ssml", OutputFormat="pcm", SampleRate="16000",
                                    VoiceId=voice, Engine=engine, LanguageCode=lang)
            return r["AudioStream"].read()

        try:
            pcm = await asyncio.to_thread(run)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"AWS Polly: {type(e).__name__}: {e}") from None
        return AudioChunk(pcm, "pcm16", 16000)


@register
class ElevenLabsTTS(TTSProvider):
    meta = ProviderMeta("tts", "elevenlabs", "ElevenLabs", C.ELEVENLABS,
                        models=["eleven_flash_v2_5", "eleven_multilingual_v2", "eleven_turbo_v2_5", "eleven_v3"],
                        default_model="eleven_flash_v2_5",
                        features=["speed", "stability", "similarity", "style-exaggeration", "voice-cloning"])

    async def synthesize(self, text, language, voice, opts, model=None):
        lang = _lang("elevenlabs", language)
        vid = voice or ELEVENLABS_DEFAULT_VOICE
        m = model or self.meta.default_model
        body = {"text": text, "model_id": m,
                "voice_settings": {"stability": _clamp(opts.stability, 0, 1),
                                   "similarity_boost": _clamp(opts.similarity, 0, 1),
                                   "style": _clamp(opts.style_exaggeration, 0, 1), "use_speaker_boost": True,
                                   "speed": _clamp(opts.speed, 0.7, 1.2)}}
        if m in ("eleven_flash_v2_5", "eleven_turbo_v2_5"):
            body["language_code"] = lang
        r = await self.http.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
                                 params={"output_format": "pcm_16000"}, json=body,
                                 headers={"xi-api-key": self.cred("api_key")})
        await self._check(r)
        return AudioChunk(r.content, "pcm16", 16000)


@register
class OpenAITTS(TTSProvider):
    meta = ProviderMeta("tts", "openai", "OpenAI TTS", C.OPENAI, models=["gpt-4o-mini-tts", "tts-1", "tts-1-hd"],
                        default_model="gpt-4o-mini-tts", features=["speed", "emotion(instructions, gpt-4o-mini-tts)"],
                        notes="OpenAI announced /audio/speech model shutdown on 2027-01-06 (replacement is Realtime-API only).")

    async def synthesize(self, text, language, voice, opts, model=None):
        _lang("openai", language)
        m = model or self.meta.default_model
        # "pcm" = raw 24 kHz 16-bit LE mono; avoids streaming-WAV headers with bogus sizes
        body = {"model": m, "voice": voice or "coral", "input": text, "response_format": "pcm",
                "speed": _clamp(opts.speed, 0.25, 4.0)}
        if m == "gpt-4o-mini-tts" and opts.style and opts.style != "neutral":
            body["instructions"] = EMOTION_INSTRUCTIONS.get(opts.style, f"Speak in a {opts.style} tone.")
        base = (self.opt_cred("base_url") or "https://api.openai.com/v1").rstrip("/")
        r = await self.http.post(f"{base}/audio/speech", json=body,
                                 headers={"Authorization": f"Bearer {self.cred('api_key')}"})
        await self._check(r)
        return AudioChunk(r.content, "pcm16", 24000)


EDGE_VOICES = {
    "hi-IN": ["hi-IN-SwaraNeural", "hi-IN-MadhurNeural"], "bn-IN": ["bn-IN-TanishaaNeural", "bn-IN-BashkarNeural"],
    "ta-IN": ["ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"], "te-IN": ["te-IN-ShrutiNeural", "te-IN-MohanNeural"],
    "mr-IN": ["mr-IN-AarohiNeural", "mr-IN-ManoharNeural"], "gu-IN": ["gu-IN-DhwaniNeural", "gu-IN-NiranjanNeural"],
    "kn-IN": ["kn-IN-SapnaNeural", "kn-IN-GaganNeural"], "ml-IN": ["ml-IN-SobhanaNeural", "ml-IN-MidhunNeural"],
    "ur-IN": ["ur-IN-GulNeural", "ur-IN-SalmanNeural"], "en-IN": ["en-IN-NeerjaNeural", "en-IN-PrabhatNeural",
                                                                    "en-IN-NeerjaExpressiveNeural"],
    "ne-IN": ["ne-NP-HemkalaNeural", "ne-NP-SagarNeural"],
}
GTTS_LANGS = {"hi-IN": "hi", "bn-IN": "bn", "ta-IN": "ta", "te-IN": "te", "mr-IN": "mr", "gu-IN": "gu",
              "kn-IN": "kn", "ml-IN": "ml", "ur-IN": "ur", "en-IN": "en", "ne-IN": "ne", "pa-IN": "pa"}


def _speech_chunk(text: str, language: str) -> AudioChunk:
    """Last-resort: ask the browser to speak the text with its own speechSynthesis voices."""
    return AudioChunk(text.encode("utf-8"), "speech", 0)


@register
class FreeTTS(TTSProvider):
    meta = ProviderMeta("tts", "free", "Free voices - no key (Microsoft Edge neural → Google Translate → browser)", [],
                        features=["no-key", "real-speech", "speed", "pitch"],
                        notes="Uses the public Microsoft Edge read-aloud voices (edge-tts), then gTTS, then the "
                              "browser's speechSynthesis. Unofficial endpoints: fine for demos, not an SLA-backed "
                              "production service.")

    async def _edge(self, text, voice, opts):
        import edge_tts  # type: ignore

        rate = f"{(_clamp(opts.speed, 0.5, 2.0) - 1) * 100:+.0f}%"
        pitch = f"{int(_clamp(opts.pitch, -10, 10) * 8):+d}Hz"
        comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, connect_timeout=6, receive_timeout=20)
        data = bytearray()
        async for ch in comm.stream():
            if ch["type"] == "audio":
                data += ch["data"]
        if not data:
            raise ProviderError("edge-tts returned no audio")
        return AudioChunk(bytes(data), "mp3", 24000)

    async def _gtts(self, text, lang):
        from gtts import gTTS  # type: ignore
        import io

        def run():
            buf = io.BytesIO()
            gTTS(text, lang=lang, tld="co.in").write_to_fp(buf)
            return buf.getvalue()

        data = await asyncio.wait_for(asyncio.to_thread(run), 15)
        return AudioChunk(data, "mp3", 24000)

    async def synthesize(self, text, language, voice, opts, model=None):
        errors = []
        voices = EDGE_VOICES.get(language)
        if voices and (model in (None, "", "edge")):
            v = voice if voice and voice.endswith("Neural") and voice[:5] == voices[0][:5] else voices[0]
            try:
                return await asyncio.wait_for(self._edge(text, v, opts), 25)
            except Exception as e:  # noqa: BLE001
                errors.append(f"edge: {type(e).__name__}")
        g = GTTS_LANGS.get(language)
        if g and model in (None, "", "edge", "gtts"):
            try:
                return await self._gtts(text, g)
            except Exception as e:  # noqa: BLE001
                errors.append(f"gtts: {type(e).__name__}")
        if errors:
            import logging
            logging.getLogger("voice.tts").warning("free TTS fell back to browser speech (%s)", ", ".join(errors))
        return _speech_chunk(text, language)


@register
class BrowserTTS(TTSProvider):
    meta = ProviderMeta("tts", "browser", "Browser speechSynthesis (device voices, no key)", [],
                        features=["no-key", "zero-server-cost", "speed", "pitch"],
                        notes="Speech is generated by the user's browser/OS; available languages depend on the device "
                              "(Chrome/Android/Windows usually have hi-IN, en-IN; many have ta/te/bn/mr...).")

    async def synthesize(self, text, language, voice, opts, model=None):
        return _speech_chunk(text, language)
