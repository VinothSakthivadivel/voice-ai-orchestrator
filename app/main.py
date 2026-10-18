"""FastAPI app: REST API (providers, languages, bot profiles), realtime WebSocket, static UI."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .languages import LANGUAGES, support_matrix
from .models import BotProfile
from .pipeline.session import CallSession
from .providers import REGISTRY, Credentials, ProviderError, TTSOptions, get_provider, list_providers
from .security import access_token, check_token, install_log_redaction, redact
from .store import ProfileStore

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
install_log_redaction()
log = logging.getLogger("voice")

STATIC = Path(__file__).resolve().parent.parent / "static"
ALLOW_ENV_KEYS = os.environ.get("ALLOW_ENV_KEYS", "true").lower() in ("1", "true", "yes")
MAX_SESSIONS = int(os.environ.get("MAX_SESSIONS", "50"))
STARTED = time.time()

app = FastAPI(title="Voice AI Orchestrator", version=__version__)
store = ProfileStore()
ACTIVE: set[CallSession] = set()


def require_token(x_access_token: str | None = Header(None), token: str | None = Query(None)):
    if not check_token(x_access_token or token):
        raise HTTPException(401, "Access token required (APP_ACCESS_TOKEN is set on the server)")


@app.get("/health")
async def health():
    return {"status": "ok", "version": __version__, "uptime_s": int(time.time() - STARTED),
            "active_sessions": len(ACTIVE)}


@app.get("/api/info")
async def info():
    env_configured = {}
    for layer in REGISTRY.values():
        for cls in layer.values():
            m = cls.meta
            req = [c for c in m.credentials if c.required and not c.default]
            env_configured[f"{m.layer}:{m.name}"] = bool(req) and ALLOW_ENV_KEYS and all(os.environ.get(c.env) for c in req)
    return {"version": __version__, "auth_required": bool(access_token()), "allow_env_keys": ALLOW_ENV_KEYS,
            "env_configured": env_configured}


@app.get("/api/providers", dependencies=[Depends(require_token)])
async def providers():
    return {"providers": list_providers()}


@app.get("/api/languages", dependencies=[Depends(require_token)])
async def languages():
    return support_matrix(list(REGISTRY["stt"]), list(REGISTRY["tts"]), list(REGISTRY["llm"]))


@app.get("/api/bots", dependencies=[Depends(require_token)])
async def list_bots():
    return {"bots": [p.model_dump() for p in store.list()]}


@app.get("/api/bots/{pid}", dependencies=[Depends(require_token)])
async def get_bot(pid: str):
    p = store.get(pid)
    if not p:
        raise HTTPException(404, "not found")
    return p.model_dump()


@app.post("/api/bots", dependencies=[Depends(require_token)])
async def save_bot(p: BotProfile):
    for layer, name in (("stt", p.stt.provider), ("llm", p.llm.provider), ("tts", p.tts.provider)):
        if name not in REGISTRY[layer]:
            raise HTTPException(400, f"unknown {layer} provider {name}")
    return store.save(p).model_dump()


@app.delete("/api/bots/{pid}", dependencies=[Depends(require_token)])
async def delete_bot(pid: str):
    return {"deleted": store.delete(pid)}


class TestReq(BaseModel):
    layer: str
    provider: str
    keys: dict = {}
    language: str = "hi-IN"
    model: str | None = None
    voice: str | None = None
    text: str | None = None


@app.post("/api/test-provider", dependencies=[Depends(require_token)])
async def test_provider(req: TestReq):
    """Quick credential/connectivity check for one provider (keys used for this request only)."""
    creds = Credentials(req.keys, allow_env=ALLOW_ENV_KEYS)
    t0 = time.perf_counter()
    try:
        prov = get_provider(req.layer, req.provider, creds)
        if req.layer == "llm":
            out = await asyncio.wait_for(prov.complete(
                [{"role": "user", "content": req.text or "Say hello in one short sentence."}],
                model=req.model, temperature=0.3, max_tokens=40, options={}), 30)
            return {"ok": True, "latency_ms": int((time.perf_counter() - t0) * 1000), "output": out}
        if req.layer == "tts":
            ch = await asyncio.wait_for(prov.synthesize(req.text or "नमस्ते, यह एक परीक्षण है।" if req.language == "hi-IN"
                                                        else (req.text or "Hello, this is a test."),
                                                        req.language, req.voice, TTSOptions(), req.model), 30)
            return {"ok": True, "latency_ms": int((time.perf_counter() - t0) * 1000), "format": ch.format,
                    "sample_rate": ch.sample_rate, "audio_b64": base64.b64encode(ch.data).decode()}
        if req.layer == "stt":
            import numpy as np
            sr = 16000
            tt = np.arange(sr) / sr
            pcm = (0.1 * np.sin(2 * np.pi * 220 * tt) * 32767).astype("<i2").tobytes()
            r = await asyncio.wait_for(prov.transcribe(pcm, sr, req.language, {"model": req.model}), 30)
            return {"ok": True, "latency_ms": int((time.perf_counter() - t0) * 1000), "output": r.text,
                    "note": "Sent 1s test tone; empty transcript is expected - this checks auth/connectivity."}
        raise HTTPException(400, "layer must be stt|llm|tts")
    except HTTPException:
        raise
    except ProviderError as e:
        return JSONResponse({"ok": False, "error": redact(str(e))}, status_code=200)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": redact(f"{type(e).__name__}: {e}")}, status_code=200)


@app.websocket("/ws/call")
async def ws_call(ws: WebSocket):
    await ws.accept()
    if not check_token(ws.query_params.get("token")):
        await ws.send_json({"type": "error", "layer": "auth", "message": "invalid access token"})
        await ws.close(code=4401)
        return
    if len(ACTIVE) >= MAX_SESSIONS:
        await ws.send_json({"type": "error", "layer": "server", "message": "server busy"})
        await ws.close(code=4429)
        return
    session: CallSession | None = None
    try:
        first = await asyncio.wait_for(ws.receive_json(), 30)
        if first.get("type") != "start":
            raise ValueError("first message must be {type:'start'}")
        if first.get("profile"):
            profile = BotProfile(**first["profile"])
        else:
            profile = store.get(first.get("profile_id") or "demo-mock") or BotProfile()
        creds = Credentials(first.get("keys") or {}, allow_env=ALLOW_ENV_KEYS)
        session = CallSession(profile, creds, ws.send_json, ws.send_bytes)
        ACTIVE.add(session)
        log.info("call started bot=%s stt=%s llm=%s tts=%s lang=%s", profile.name, profile.stt.provider,
                 profile.llm.provider, profile.tts.provider, profile.language)
        await session.start()
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await session.on_audio(msg["bytes"])
                continue
            data = json.loads(msg.get("text") or "{}")
            t = data.get("type")
            if t == "text":
                await session.on_text(data.get("text", ""))
            elif t == "playback_ended":
                await session.on_playback_ended(data.get("turn"))
            elif t == "interrupt":
                await session.interrupt("client")
            elif t == "enroll_speaker":
                await session.enroll_speaker()
            elif t == "update":  # live control changes (temperature, speed, ...)
                merged = session.p.model_dump()
                for k, v in (data.get("profile") or {}).items():
                    if isinstance(v, dict) and isinstance(merged.get(k), dict):
                        merged[k].update(v)
                    else:
                        merged[k] = v
                session.p = BotProfile(**merged)
                session.speaker.enabled = session.p.speaker.enabled
                session.speaker.threshold = session.p.speaker.threshold
                await session.send({"type": "updated"})
            elif t == "stop":
                break
            elif t == "ping":
                await session.send({"type": "pong", "t": data.get("t")})
    except WebSocketDisconnect:
        pass
    except Exception as e:  # noqa: BLE001
        log.warning("ws error: %s", redact(f"{type(e).__name__}: {e}"))
        try:
            await ws.send_json({"type": "error", "layer": "server", "message": redact(str(e))})
        except Exception:
            pass
    finally:
        if session:
            log.info("call ended stats=%s", session.stats)
            await session.close()
            ACTIVE.discard(session)
        try:
            await ws.close()
        except Exception:
            pass


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(STATIC / "favicon.svg", media_type="image/svg+xml")


@app.on_event("startup")
async def _startup_checks():
    env_keys = [c.env for layer in REGISTRY.values() for cls in layer.values() for c in cls.meta.credentials
                if c.secret and os.environ.get(c.env)]
    if env_keys and ALLOW_ENV_KEYS and not access_token():
        log.warning("Provider keys are configured via env vars (%d) but APP_ACCESS_TOKEN is not set: anyone who can "
                    "reach this URL can spend your provider credits. Set APP_ACCESS_TOKEN.", len(env_keys))
