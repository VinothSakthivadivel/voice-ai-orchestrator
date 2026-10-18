"""Bot profile persistence (JSON file). Credentials are never stored here."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

from .models import BotProfile

_SECRET_NAMES = {"api_key", "apikey", "key", "keys", "secret", "secret_access_key", "access_key_id", "session_token",
                 "token", "password", "authorization"}


def _scrub(d):
    if isinstance(d, dict):
        return {k: _scrub(v) for k, v in d.items() if k.lower() not in _SECRET_NAMES}
    if isinstance(d, list):
        return [_scrub(x) for x in d]
    return d


def default_profiles() -> list[BotProfile]:
    return [
        BotProfile(id="demo-free", name="Demo - real voice (no keys)", language="en-IN",
                   description="Speaks with free Microsoft Edge neural voices (no key). STT/LLM are mocks: it echoes "
                               "what you say/type. Change language to hi-IN, ta-IN, te-IN... to hear Indian voices.",
                   llm={"provider": "mock", "greeting": "Hello! I am the demo voice bot. Say something, or type a "
                                                        "message below, and I will answer out loud."},
                   tts={"provider": "free"}),
        BotProfile(id="demo-mock", name="Mock tones (offline pipeline test)", language="en-IN",
                   description="Fully offline mock STT/LLM/TTS. TTS plays placeholder TONES, not speech.",
                   llm={"provider": "mock", "greeting": "Hello! I am the offline mock bot."}),
        BotProfile(id="sarvam-hindi", name="Hindi support agent (Sarvam)", language="hi-IN",
                   description="All-Sarvam stack, best for Indic languages.",
                   stt={"provider": "sarvam"}, llm={"provider": "sarvam", "temperature": 0.4,
                        "greeting": "नमस्ते! मैं आपकी कैसे मदद कर सकती हूँ?"},
                   tts={"provider": "sarvam", "voice": "ritu"}),
        BotProfile(id="azure-tamil", name="Tamil banking assistant (Azure + OpenAI)", language="ta-IN",
                   stt={"provider": "azure"}, llm={"provider": "openai", "model": "gpt-4o-mini"},
                   tts={"provider": "azure", "voice": "ta-IN-PallaviNeural", "style": "empathetic"}),
        BotProfile(id="fast-english", name="Low-latency English-India (Deepgram + Groq + ElevenLabs)", language="en-IN",
                   stt={"provider": "deepgram"}, llm={"provider": "groq"}, tts={"provider": "elevenlabs"}),
    ]


def _is_old_mock_demo(v) -> bool:
    return bool(v) and v.get("name") == "Demo (no keys, mock providers)"


class ProfileStore:
    def __init__(self, path: str | None = None):
        data_dir = Path(path or os.environ.get("DATA_DIR", "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.file = data_dir / "bots.json"
        self._lock = threading.Lock()
        self.ensure_defaults()

    def ensure_defaults(self):
        """Seed built-in profiles when the file is missing, empty or corrupt (fresh Render instance)."""
        with self._lock:
            d = self._read()
            builtin_demo = ("demo-free", "demo-mock")
            if not d or any(i not in d for i in builtin_demo) or _is_old_mock_demo(d.get("demo-mock")):
                fresh = not d
                for p in default_profiles():
                    if fresh or p.id in builtin_demo:
                        if p.id == "demo-mock" and _is_old_mock_demo(d.get("demo-mock")):
                            d[p.id] = p.model_dump()  # relabel v1.0.0 demo so users know it plays tones
                        d.setdefault(p.id, p.model_dump())
                # keep demos first in the list
                d = {**{i: d[i] for i in builtin_demo if i in d}, **{k: v for k, v in d.items() if k not in builtin_demo}}
                try:
                    self._write(d)
                except OSError:
                    pass

    def _read(self) -> dict:
        try:
            return json.loads(self.file.read_text("utf-8"))
        except Exception:
            return {}

    def _write(self, d: dict):
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.file)

    def list(self) -> list[BotProfile]:
        d = self._read()
        if not d:
            self.ensure_defaults()
            d = self._read() or {p.id: p.model_dump() for p in default_profiles()}
        out = []
        for v in d.values():
            try:
                out.append(BotProfile(**v))
            except Exception:  # skip a corrupt entry rather than breaking the UI
                continue
        return out

    def get(self, pid: str) -> BotProfile | None:
        v = self._read().get(pid)
        if not v:
            v = next((p.model_dump() for p in default_profiles() if p.id == pid), None)
        return BotProfile(**v) if v else None

    def save(self, p: BotProfile) -> BotProfile:
        with self._lock:
            d = self._read()
            p.id = p.id or uuid.uuid4().hex[:10]
            p.updated_at = time.time()
            d[p.id] = _scrub(p.model_dump())
            self._write(d)
        return BotProfile(**d[p.id])

    def delete(self, pid: str) -> bool:
        with self._lock:
            d = self._read()
            ok = d.pop(pid, None) is not None
            self._write(d)
            return ok
