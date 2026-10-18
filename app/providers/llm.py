"""LLM providers with token streaming (SSE)."""
from __future__ import annotations

import asyncio
import json
import re
from typing import AsyncIterator

from . import creds as C
from .base import LLMProvider, ProviderError, ProviderMeta, register


async def _sse_lines(resp) -> AsyncIterator[str]:
    async for line in resp.aiter_lines():
        line = line.strip()
        if line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                yield data


class OpenAICompatible(LLMProvider):
    """Any /chat/completions endpoint (OpenAI, Groq, Mistral, OpenRouter, Sarvam, vLLM, Ollama...)."""
    base_url = "https://api.openai.com/v1"

    def _url(self) -> str:
        base = (self.opt_cred("base_url") or self.base_url).rstrip("/")
        return f"{base}/chat/completions"

    key_required = True

    def _headers(self) -> dict:
        key = self.cred("api_key") if self.key_required else self.opt_cred("api_key")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _body(self, messages, model, temperature, max_tokens, options) -> dict:
        body = {"model": model or self.meta.default_model, "messages": messages, "stream": True,
                "temperature": temperature, "max_tokens": max_tokens}
        if options.get("top_p") is not None:
            body["top_p"] = options["top_p"]
        return body

    async def stream(self, messages, *, model, temperature, max_tokens, options):
        body = self._body(messages, model, temperature, max_tokens, options)
        async with self.http.stream("POST", self._url(), json=body, headers=self._headers()) as r:
            await self._check(r)
            async for data in _sse_lines(r):
                try:
                    j = json.loads(data)
                except json.JSONDecodeError:
                    continue
                for ch in j.get("choices") or []:
                    t = (ch.get("delta") or {}).get("content")
                    if t:
                        yield t


@register
class OpenAILLM(OpenAICompatible):
    meta = ProviderMeta("llm", "openai", "OpenAI", C.OPENAI,
                        models=["gpt-4o-mini", "gpt-6-luna", "gpt-4.1-mini", "gpt-5-mini"],
                        default_model="gpt-4o-mini",
                        notes="gpt-4o-mini/4.1 shut down 2027-01-14 (OpenAI recommends gpt-6-luna). Reasoning models "
                              "(gpt-5*/gpt-6*/o*) ignore temperature.")

    def _body(self, messages, model, temperature, max_tokens, options):
        b = super()._body(messages, model, temperature, max_tokens, options)
        m = b["model"] or ""
        if m.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):  # reasoning models: no temperature, max_completion_tokens
            b.pop("temperature", None)
            b["max_completion_tokens"] = b.pop("max_tokens")
        return b


@register
class GroqLLM(OpenAICompatible):
    base_url = "https://api.groq.com/openai/v1"
    meta = ProviderMeta("llm", "groq", "Groq", C.GROQ,
                        models=["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "openai/gpt-oss-20b"],
                        default_model="llama-3.3-70b-versatile", features=["ultra-low-latency"])


@register
class MistralLLM(OpenAICompatible):
    base_url = "https://api.mistral.ai/v1"
    meta = ProviderMeta("llm", "mistral", "Mistral AI", C.MISTRAL,
                        models=["mistral-small-latest", "mistral-large-latest", "ministral-8b-latest"],
                        default_model="mistral-small-latest")


@register
class OpenRouterLLM(OpenAICompatible):
    base_url = "https://openrouter.ai/api/v1"
    meta = ProviderMeta("llm", "openrouter", "OpenRouter (any model)", C.OPENROUTER,
                        models=["openai/gpt-4o-mini", "anthropic/claude-3.5-haiku", "google/gemini-2.0-flash-001",
                                "meta-llama/llama-3.3-70b-instruct"],
                        default_model="openai/gpt-4o-mini")


@register
class SarvamLLM(OpenAICompatible):
    base_url = "https://api.sarvam.ai/v1"
    meta = ProviderMeta("llm", "sarvam", "Sarvam AI (Sarvam-105B)", C.SARVAM,
                        models=["sarvam-105b-conversations", "sarvam-105b"],
                        default_model="sarvam-105b-conversations", features=["indic-first", "voice-agent-tuned"],
                        notes="options.reasoning_effort: low | high | max (default low for voice latency).")

    def _body(self, messages, model, temperature, max_tokens, options):
        b = super()._body(messages, model, temperature, max_tokens, options)
        b["reasoning_effort"] = options.get("reasoning_effort") or "low"
        return b

    def _headers(self):
        key = self.cred("api_key")
        return {"api-subscription-key": key, "Authorization": f"Bearer {key}"}


@register
class CustomOpenAILLM(OpenAICompatible):
    meta = ProviderMeta("llm", "openai_compatible", "Custom OpenAI-compatible (vLLM/Ollama/Together...)",
                        C.OPENAI_COMPAT, models=[], default_model=None)
    key_required = False

    def _url(self):
        return f"{self.cred('base_url').rstrip('/')}/chat/completions"


@register
class AzureOpenAILLM(OpenAICompatible):
    meta = ProviderMeta("llm", "azure_openai", "Azure OpenAI", C.AZURE_OPENAI, models=[],
                        notes="Model = your deployment name (falls back to the deployment credential).")

    def _url(self):
        ep = self.cred("endpoint").rstrip("/")
        return f"{ep}/openai/deployments/{self._deployment}/chat/completions?api-version={self.cred('api_version')}"

    def _headers(self):
        return {"api-key": self.cred("api_key")}

    async def stream(self, messages, *, model, temperature, max_tokens, options):
        self._deployment = model or self.cred("deployment")
        async for t in super().stream(messages, model=self._deployment, temperature=temperature,
                                      max_tokens=max_tokens, options=options):
            yield t


def _split_system(messages):
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    rest = [m for m in messages if m["role"] != "system"]
    # merge consecutive same-role turns (Anthropic/Gemini/Bedrock require alternation)
    merged: list[dict] = []
    for m in rest:
        if merged and merged[-1]["role"] == m["role"]:
            merged[-1] = {"role": m["role"], "content": merged[-1]["content"] + "\n" + m["content"]}
        else:
            merged.append(dict(m))
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return system, merged


def _anthropic_accepts_temperature(model: str) -> bool:
    return bool(re.search(r"claude-3|-4-[0-6]\b|-4-[0-6]-|sonnet-4$|opus-4$|-4-\d{8}", model))


@register
class AnthropicLLM(LLMProvider):
    meta = ProviderMeta("llm", "anthropic", "Anthropic Claude", C.ANTHROPIC,
                        models=["claude-haiku-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"],
                        default_model="claude-haiku-5-5",
                        notes="Claude 4.7+/5.x models reject temperature/top_p; it is sent only to older models.")

    async def stream(self, messages, *, model, temperature, max_tokens, options):
        system, msgs = _split_system(messages)
        m = model or self.meta.default_model
        body = {"model": m, "max_tokens": max_tokens, "messages": msgs, "stream": True}
        if _anthropic_accepts_temperature(m):
            body["temperature"] = temperature
        if system:
            body["system"] = system
        h = {"x-api-key": self.cred("api_key"), "anthropic-version": "2023-06-01"}
        async with self.http.stream("POST", "https://api.anthropic.com/v1/messages", json=body, headers=h) as r:
            await self._check(r)
            async for data in _sse_lines(r):
                j = json.loads(data)
                if j.get("type") == "content_block_delta" and j.get("delta", {}).get("type", "text_delta") == "text_delta":
                    t = j.get("delta", {}).get("text")
                    if t:
                        yield t
                elif j.get("type") == "error":
                    raise ProviderError(f"Anthropic: {j.get('error')}")


@register
class GeminiLLM(LLMProvider):
    meta = ProviderMeta("llm", "gemini", "Google Gemini", C.GEMINI,
                        models=["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-2.5-flash"],
                        default_model="gemini-3.8-flash",
                        notes="Gemini 3.x: thinkingLevel=low is sent by default to keep voice latency down.")

    async def stream(self, messages, *, model, temperature, max_tokens, options):
        system, msgs = _split_system(messages)
        body = {"contents": [{"role": "model" if m["role"] == "assistant" else "user",
                              "parts": [{"text": m["content"]}]} for m in msgs],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        m = model or self.meta.default_model
        if m.startswith("gemini-3"):
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": options.get("thinking_level") or "low"}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:streamGenerateContent"
        async with self.http.stream("POST", url, params={"alt": "sse"}, json=body,
                                    headers={"x-goog-api-key": self.cred("api_key")}) as r:
            await self._check(r)
            async for data in _sse_lines(r):
                j = json.loads(data)
                for c in j.get("candidates") or []:
                    for p in (c.get("content") or {}).get("parts") or []:
                        if p.get("text"):
                            yield p["text"]


@register
class BedrockLLM(LLMProvider):
    meta = ProviderMeta("llm", "bedrock", "AWS Bedrock (Converse API)", C.AWS, cred_group="aws",
                        models=["amazon.nova-lite-v1:0", "anthropic.claude-haiku-5-5", "meta.llama3-1-8b-instruct-v1:0",
                                "mistral.mistral-small-2402-v1:0"],
                        default_model="amazon.nova-lite-v1:0",
                        notes="Use a model / inference-profile id enabled in your region.")

    async def stream(self, messages, *, model, temperature, max_tokens, options):
        import boto3  # local import keeps startup fast

        system, msgs = _split_system(messages)
        client = boto3.client("bedrock-runtime", region_name=self.cred("region"),
                              aws_access_key_id=self.cred("access_key_id"),
                              aws_secret_access_key=self.cred("secret_access_key"),
                              aws_session_token=self.opt_cred("session_token"))
        kw = {"modelId": model or self.meta.default_model,
              "messages": [{"role": m["role"], "content": [{"text": m["content"]}]} for m in msgs],
              "inferenceConfig": {"temperature": temperature, "maxTokens": max_tokens}}
        if system:
            kw["system"] = [{"text": system}]
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        stop = {"flag": False}

        def run():
            try:
                resp = client.converse_stream(**kw)
                for ev in resp["stream"]:
                    if stop["flag"]:
                        break
                    t = ev.get("contentBlockDelta", {}).get("delta", {}).get("text")
                    if t:
                        loop.call_soon_threadsafe(q.put_nowait, t)
                loop.call_soon_threadsafe(q.put_nowait, None)
            except Exception as e:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, ProviderError(f"Bedrock: {type(e).__name__}: {e}"))

        fut = loop.run_in_executor(None, run)
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            stop["flag"] = True
            fut.cancel()
