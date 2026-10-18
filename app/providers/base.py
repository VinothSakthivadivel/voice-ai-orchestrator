"""Provider abstraction + registry for STT / LLM / TTS layers (bring your own keys)."""
from __future__ import annotations

import io
import os
import wave
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

import httpx


class ProviderError(RuntimeError):
    """Raised for provider failures. Messages never include credentials."""


class MissingCredential(ProviderError):
    pass


@dataclass
class CredField:
    name: str          # key inside the session credential dict
    env: str           # environment variable fallback
    label: str
    secret: bool = True
    required: bool = True
    default: str | None = None


@dataclass
class ProviderMeta:
    layer: str
    name: str
    label: str
    credentials: list[CredField]
    models: list[str] = field(default_factory=list)
    default_model: str | None = None
    features: list[str] = field(default_factory=list)
    notes: str = ""
    cred_group: str | None = None  # credentials shared between layers (e.g. "azure_speech")

    def public(self) -> dict:
        return {
            "layer": self.layer, "name": self.name, "label": self.label,
            "cred_group": self.group,
            "credentials": [
                {"name": c.name, "env": c.env, "label": c.label, "secret": c.secret,
                 "required": c.required, "default": c.default} for c in self.credentials
            ],
            "models": self.models, "default_model": self.default_model,
            "features": self.features, "notes": self.notes,
        }

    @property
    def group(self) -> str:
        return self.cred_group or self.name


class Credentials:
    """Resolves credentials: per-session keys first, then env vars (if allowed)."""

    def __init__(self, session_keys: dict[str, dict[str, str]] | None = None, allow_env: bool = True):
        self._keys = {g: {k: v for k, v in (d or {}).items() if v} for g, d in (session_keys or {}).items()}
        self.allow_env = allow_env

    def get(self, meta: ProviderMeta, field_name: str) -> str | None:
        f = next((c for c in meta.credentials if c.name == field_name), None)
        val = self._keys.get(meta.group, {}).get(field_name)
        if not val and f and self.allow_env:
            val = os.environ.get(f.env)
        if not val and f:
            val = f.default
        return val

    def require(self, meta: ProviderMeta, field_name: str) -> str:
        v = self.get(meta, field_name)
        if not v:
            f = next((c for c in meta.credentials if c.name == field_name), None)
            env = f.env if f else "?"
            raise MissingCredential(f"{meta.label}: missing '{field_name}' (enter it in the Keys tab or set {env})")
        return v

    def secrets(self) -> list[str]:
        out = [v for d in self._keys.values() for v in d.values() if v and len(v) >= 6]
        return out

    def __repr__(self) -> str:  # never print keys
        return f"Credentials(groups={sorted(self._keys)}, allow_env={self.allow_env})"


# --------------------------------------------------------------------------- results
@dataclass
class STTResult:
    text: str
    language: str | None = None
    confidence: float | None = None
    words: list[dict[str, Any]] = field(default_factory=list)  # may carry "speaker"


@dataclass
class TTSOptions:
    speed: float = 1.0          # 0.5 - 2.0
    pitch: float = 0.0          # semitones-ish, -10..10 (mapped per provider)
    style: str | None = None    # emotion / speaking style e.g. cheerful, empathetic
    style_degree: float = 1.0   # Azure styledegree
    stability: float = 0.5      # ElevenLabs
    similarity: float = 0.75    # ElevenLabs
    style_exaggeration: float = 0.0  # ElevenLabs "style"
    volume: float = 1.0
    sample_rate: int = 16000


@dataclass
class AudioChunk:
    data: bytes
    format: str  # "wav" | "mp3" | "pcm16"
    sample_rate: int = 16000


# --------------------------------------------------------------------------- base classes
class BaseProvider:
    meta: ProviderMeta

    def __init__(self, creds: Credentials, http: httpx.AsyncClient | None = None):
        self.creds = creds
        self._http = http

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = shared_http()
        return self._http

    def cred(self, name: str) -> str:
        return self.creds.require(self.meta, name)

    def opt_cred(self, name: str) -> str | None:
        return self.creds.get(self.meta, name)

    async def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            body = (await r.aread()).decode("utf-8", "replace")[:400]
            for s in self.creds.secrets():
                body = body.replace(s, "***")
            raise ProviderError(f"{self.meta.label} HTTP {r.status_code}: {body}")
        return r


class STTProvider(BaseProvider):
    async def transcribe(self, pcm16: bytes, sample_rate: int, language: str, options: dict) -> STTResult:
        raise NotImplementedError


class LLMProvider(BaseProvider):
    def stream(self, messages: list[dict], *, model: str | None, temperature: float,
               max_tokens: int, options: dict) -> AsyncIterator[str]:
        raise NotImplementedError

    async def complete(self, messages: list[dict], **kw) -> str:
        out = []
        async for t in self.stream(messages, **kw):
            out.append(t)
        return "".join(out)


class TTSProvider(BaseProvider):
    async def synthesize(self, text: str, language: str, voice: str | None, opts: TTSOptions,
                         model: str | None = None) -> AudioChunk:
        raise NotImplementedError


# --------------------------------------------------------------------------- registry
REGISTRY: dict[str, dict[str, type[BaseProvider]]] = {"stt": {}, "llm": {}, "tts": {}}


def register(cls: type[BaseProvider]) -> type[BaseProvider]:
    REGISTRY[cls.meta.layer][cls.meta.name] = cls
    return cls


def get_provider(layer: str, name: str, creds: Credentials, http: httpx.AsyncClient | None = None):
    try:
        cls = REGISTRY[layer][name]
    except KeyError:
        raise ProviderError(f"Unknown {layer} provider '{name}'") from None
    return cls(creds, http)


def list_providers(layer: str | None = None) -> list[dict]:
    layers = [layer] if layer else ["stt", "llm", "tts"]
    return [cls.meta.public() for l in layers for cls in REGISTRY[l].values()]


_HTTP: httpx.AsyncClient | None = None


def shared_http() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None or _HTTP.is_closed:
        _HTTP = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0),
                                  limits=httpx.Limits(max_connections=100, max_keepalive_connections=20))
    return _HTTP


# --------------------------------------------------------------------------- audio helpers
def pcm_to_wav(pcm16: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm16)
    return buf.getvalue()


def wav_info(data: bytes) -> tuple[int, bytes] | None:
    """Return (sample_rate, pcm) for a mono 16-bit WAV, else None."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.getframerate(), w.readframes(w.getnframes())
    except Exception:
        return None
