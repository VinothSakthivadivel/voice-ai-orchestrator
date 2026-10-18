"""Speech-to-text providers. Utterance-level (post-VAD endpointing) REST calls."""
from __future__ import annotations

import asyncio
import base64

from ..languages import provider_code
from . import creds as C
from .base import (MissingCredential, ProviderError, ProviderMeta, STTProvider, STTResult,
                   pcm_to_wav, register)


def _lang(name: str, language: str) -> str | None:
    code = provider_code("stt", name, language)
    if code is None:
        raise ProviderError(f"STT provider '{name}' does not support language {language}")
    return None if code == "auto" else code


@register
class SarvamSTT(STTProvider):
    meta = ProviderMeta("stt", "sarvam", "Sarvam AI (Saaras)", C.SARVAM,
                        models=["saaras:v4", "saaras:v3"], default_model="saaras:v4",
                        features=["22-indic-languages", "auto-language-detect", "code-mixed", "modes"],
                        notes="options.mode (saaras:v3): transcribe | translate | verbatim | translit | codemix")

    async def transcribe(self, pcm16, sample_rate, language, options):
        lang = _lang("sarvam", language) or "unknown"
        files = {"file": ("audio.wav", pcm_to_wav(pcm16, sample_rate), "audio/wav")}
        data = {"model": options.get("model") or self.meta.default_model, "language_code": lang}
        if options.get("mode"):
            data["mode"] = options["mode"]
        r = await self.http.post("https://api.sarvam.ai/speech-to-text", files=files, data=data,
                                 headers={"api-subscription-key": self.cred("api_key")})
        await self._check(r)
        j = r.json()
        return STTResult(text=j.get("transcript", ""), language=j.get("language_code"))


@register
class GoogleSTT(STTProvider):
    meta = ProviderMeta("stt", "google", "Google Cloud Speech-to-Text", C.GOOGLE_CLOUD,
                        models=["latest_long", "latest_short", "default"], default_model="latest_short",
                        features=["diarization(optional)"], cred_group="google_cloud")

    async def transcribe(self, pcm16, sample_rate, language, options):
        lang = _lang("google", language) or "en-IN"
        cfg = {"encoding": "LINEAR16", "sampleRateHertz": sample_rate, "languageCode": lang,
               "enableAutomaticPunctuation": True}
        model = options.get("model") or self.meta.default_model
        if model and model != "default":
            cfg["model"] = model
        alt = options.get("alt_languages")
        if alt:
            cfg["alternativeLanguageCodes"] = alt[:3]
        if options.get("diarization"):
            cfg["diarizationConfig"] = {"enableSpeakerDiarization": True, "minSpeakerCount": 1,
                                        "maxSpeakerCount": 4}
        body = {"config": cfg, "audio": {"content": base64.b64encode(pcm16).decode()}}
        r = await self.http.post("https://speech.googleapis.com/v1/speech:recognize",
                                 params={"key": self.cred("api_key")}, json=body)
        await self._check(r)
        res = r.json().get("results", [])
        text = " ".join(x["alternatives"][0].get("transcript", "") for x in res if x.get("alternatives"))
        words = []
        if res and options.get("diarization"):
            for w in res[-1]["alternatives"][0].get("words", []):
                words.append({"word": w.get("word"), "speaker": w.get("speakerTag")})
        conf = res[0]["alternatives"][0].get("confidence") if res and res[0].get("alternatives") else None
        return STTResult(text=text.strip(), language=lang, confidence=conf, words=words)


@register
class AzureSTT(STTProvider):
    meta = ProviderMeta("stt", "azure", "Azure AI Speech", C.AZURE_SPEECH,
                        features=["profanity-filter", "detailed-confidence"], cred_group="azure_speech")

    async def transcribe(self, pcm16, sample_rate, language, options):
        lang = _lang("azure", language) or "en-IN"
        region = self.cred("region")
        url = (f"https://{region}.stt.speech.microsoft.com/speech/recognition/conversation/"
               "cognitiveservices/v1")
        r = await self.http.post(url, params={"language": lang, "format": "detailed"},
                                 content=pcm_to_wav(pcm16, sample_rate),
                                 headers={"Ocp-Apim-Subscription-Key": self.cred("key"),
                                          "Content-Type": f"audio/wav; codecs=audio/pcm; samplerate={sample_rate}",
                                          "Accept": "application/json"})
        await self._check(r)
        j = r.json()
        if j.get("RecognitionStatus") not in ("Success", None):
            return STTResult(text="", language=lang)
        best = (j.get("NBest") or [{}])[0]
        return STTResult(text=best.get("Display") or j.get("DisplayText", ""), language=lang,
                         confidence=best.get("Confidence"))


@register
class AWSTranscribeSTT(STTProvider):
    meta = ProviderMeta("stt", "aws", "AWS Transcribe (streaming)", C.AWS,
                        features=["streaming-sdk"], cred_group="aws",
                        notes="Uses amazon-transcribe streaming SDK; Indian streaming support is limited to hi-IN/en-IN.")

    async def transcribe(self, pcm16, sample_rate, language, options):
        lang = _lang("aws", language) or "en-IN"
        try:
            from amazon_transcribe.auth import StaticCredentialResolver
            from amazon_transcribe.client import TranscribeStreamingClient
            from amazon_transcribe.handlers import TranscriptResultStreamHandler
        except ImportError as e:  # pragma: no cover
            raise ProviderError("amazon-transcribe package not installed") from e

        resolver = StaticCredentialResolver(self.cred("access_key_id"), self.cred("secret_access_key"),
                                            self.opt_cred("session_token"))
        client = TranscribeStreamingClient(region=self.cred("region"), credential_resolver=resolver)
        stream = await client.start_stream_transcription(language_code=lang, media_sample_rate_hz=sample_rate,
                                                         media_encoding="pcm")
        parts: list[str] = []

        class H(TranscriptResultStreamHandler):
            async def handle_transcript_event(self, ev):
                for res in ev.transcript.results:
                    if not res.is_partial and res.alternatives:
                        parts.append(res.alternatives[0].transcript)

        async def feed():
            step = 3200
            for i in range(0, len(pcm16), step):
                await stream.input_stream.send_audio_event(audio_chunk=pcm16[i:i + step])
            await stream.input_stream.end_stream()

        await asyncio.gather(feed(), H(stream.output_stream).handle_events())
        return STTResult(text=" ".join(parts).strip(), language=lang)


@register
class DeepgramSTT(STTProvider):
    meta = ProviderMeta("stt", "deepgram", "Deepgram", C.DEEPGRAM, models=["nova-3", "nova-2"],
                        default_model="nova-3", features=["diarization", "smart-format"])

    async def transcribe(self, pcm16, sample_rate, language, options):
        lang = _lang("deepgram", language)
        params = {"model": options.get("model") or self.meta.default_model, "smart_format": "true",
                  "punctuate": "true"}
        if lang:
            params["language"] = lang
        else:
            params["detect_language"] = "true"
        if options.get("diarization"):
            params["diarize"] = "true"
        r = await self.http.post("https://api.deepgram.com/v1/listen", params=params,
                                 content=pcm_to_wav(pcm16, sample_rate),
                                 headers={"Authorization": f"Token {self.cred('api_key')}",
                                          "Content-Type": "audio/wav"})
        await self._check(r)
        ch = r.json()["results"]["channels"][0]
        alt = ch["alternatives"][0]
        words = [{"word": w.get("punctuated_word", w.get("word")), "speaker": w.get("speaker")}
                 for w in alt.get("words", [])]
        return STTResult(text=alt.get("transcript", ""), language=ch.get("detected_language", lang),
                         confidence=alt.get("confidence"), words=words)


@register
class OpenAISTT(STTProvider):
    meta = ProviderMeta("stt", "openai", "OpenAI (gpt-4o-transcribe / Whisper)", C.OPENAI,
                        models=["gpt-4o-mini-transcribe", "gpt-transcribe", "gpt-4o-transcribe", "whisper-1"],
                        default_model="gpt-4o-mini-transcribe", features=["auto-language-detect"],
                        notes="OpenAI announced gpt-4o-*-transcribe/whisper-1 shutdown on 2027-02-26; switch the model to gpt-transcribe.")

    async def transcribe(self, pcm16, sample_rate, language, options):
        lang = _lang("openai", language)
        base = (self.opt_cred("base_url") or "https://api.openai.com/v1").rstrip("/")
        data = {"model": options.get("model") or self.meta.default_model}
        if lang:
            data["language"] = lang
        if options.get("prompt"):
            data["prompt"] = options["prompt"]
        r = await self.http.post(f"{base}/audio/transcriptions",
                                 files={"file": ("audio.wav", pcm_to_wav(pcm16, sample_rate), "audio/wav")},
                                 data=data, headers={"Authorization": f"Bearer {self.cred('api_key')}"})
        await self._check(r)
        return STTResult(text=r.json().get("text", "").strip(), language=lang)


@register
class AssemblyAISTT(STTProvider):
    meta = ProviderMeta("stt", "assemblyai", "AssemblyAI", C.ASSEMBLYAI, models=["universal", "best", "nano"],
                        default_model="universal", features=["diarization", "auto-language-detect"],
                        notes="Async upload+poll API: higher latency (~1-3s) than streaming providers.")

    async def transcribe(self, pcm16, sample_rate, language, options):
        lang = _lang("assemblyai", language)
        h = {"authorization": self.cred("api_key")}
        base = "https://api.assemblyai.com/v2"
        r = await self.http.post(f"{base}/upload", content=pcm_to_wav(pcm16, sample_rate), headers=h)
        await self._check(r)
        body = {"audio_url": r.json()["upload_url"], "speech_model": options.get("model") or self.meta.default_model}
        if lang:
            body["language_code"] = lang
        else:
            body["language_detection"] = True
        if options.get("diarization"):
            body["speaker_labels"] = True
        r = await self.http.post(f"{base}/transcript", json=body, headers=h)
        await self._check(r)
        tid = r.json()["id"]
        for _ in range(120):
            await asyncio.sleep(0.4)
            r = await self.http.get(f"{base}/transcript/{tid}", headers=h)
            await self._check(r)
            j = r.json()
            if j["status"] == "completed":
                words = [{"word": w["text"], "speaker": w.get("speaker")} for w in j.get("words") or []]
                return STTResult(text=j.get("text") or "", language=j.get("language_code", lang),
                                 confidence=j.get("confidence"), words=words)
            if j["status"] == "error":
                raise ProviderError(f"AssemblyAI: {j.get('error')}")
        raise ProviderError("AssemblyAI: timeout")


__all__ = ["MissingCredential"]
