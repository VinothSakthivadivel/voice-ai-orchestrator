"""Bot profile configuration schema (pydantic)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class STTConfig(BaseModel):
    provider: str = "mock"
    model: str | None = None
    diarization: bool = False
    options: dict = Field(default_factory=dict)


class LLMConfig(BaseModel):
    provider: str = "mock"
    model: str | None = None
    temperature: float = Field(0.6, ge=0, le=2)
    max_tokens: int = Field(300, ge=16, le=8192)
    top_p: float | None = None
    system_prompt: str = ("You are a helpful, concise voice assistant for Indian customers. Reply in the same "
                          "language the user speaks (code-mixing is fine). Keep answers short: 1-3 sentences, "
                          "no markdown, no lists, no emojis - your text is spoken aloud.")
    greeting: str = ""
    history_turns: int = Field(12, ge=0, le=100)
    options: dict = Field(default_factory=dict)


class TTSConfig(BaseModel):
    provider: str = "mock"
    model: str | None = None
    voice: str | None = None
    speed: float = Field(1.0, ge=0.25, le=4)
    pitch: float = Field(0.0, ge=-10, le=10)
    style: str | None = None
    style_degree: float = 1.0
    stability: float = 0.5
    similarity: float = 0.75
    style_exaggeration: float = 0.0
    volume: float = 1.0


class VADConfig(BaseModel):
    mode: str = "energy"            # energy | webrtc
    margin_db: float = 10.0         # dB above adaptive noise floor
    min_db: float = -50.0
    min_speech_ms: int = 200
    end_silence_ms: int = 700       # endpointing silence
    barge_in: bool = True
    barge_in_ms: int = 300          # sustained user speech needed to interrupt the bot
    max_utterance_ms: int = 30000


class AudioConfig(BaseModel):
    noise_reduction: bool = True    # server-side spectral noise reduction before STT
    noise_strength: float = 1.5


class SpeakerConfig(BaseModel):
    enabled: bool = False           # primary-speaker lock (ignore other voices)
    threshold: float = 0.80
    enroll_seconds: float = 3.0
    use_provider_diarization: bool = False


class SentimentConfig(BaseModel):
    mode: str = "lexicon"           # lexicon | llm | off


class BotProfile(BaseModel):
    id: str | None = None
    name: str = "Default bot"
    description: str = ""
    language: str = "hi-IN"         # canonical code, or "auto"
    stt: STTConfig = Field(default_factory=STTConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    tts: TTSConfig = Field(default_factory=TTSConfig)
    vad: VADConfig = Field(default_factory=VADConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    speaker: SpeakerConfig = Field(default_factory=SpeakerConfig)
    sentiment: SentimentConfig = Field(default_factory=SentimentConfig)
    updated_at: float | None = None
