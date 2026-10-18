import pytest

from app.providers import REGISTRY, Credentials, MissingCredential, get_provider, list_providers
from app.providers.base import ProviderError

EXPECTED = {
    "stt": {"sarvam", "google", "azure", "aws", "deepgram", "openai", "assemblyai", "mock"},
    "llm": {"openai", "anthropic", "gemini", "azure_openai", "bedrock", "groq", "mistral", "sarvam", "openrouter",
            "openai_compatible", "mock"},
    "tts": {"sarvam", "google", "azure", "aws", "elevenlabs", "openai", "mock"},
}


def test_all_providers_registered():
    for layer, names in EXPECTED.items():
        assert names <= set(REGISTRY[layer]), (layer, names - set(REGISTRY[layer]))


def test_public_meta_has_no_secrets(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secretsecretsecret1234")
    blob = str(list_providers())
    assert "sk-secret" not in blob
    assert "OPENAI_API_KEY" in blob  # env var *name* is fine


def test_unknown_provider():
    with pytest.raises(ProviderError):
        get_provider("stt", "nope", Credentials())


def test_session_keys_override_env(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "env-key-123456")
    p = get_provider("tts", "sarvam", Credentials({"sarvam": {"api_key": "session-key-999"}}))
    assert p.cred("api_key") == "session-key-999"
    p2 = get_provider("tts", "sarvam", Credentials())
    assert p2.cred("api_key") == "env-key-123456"
    p3 = get_provider("tts", "sarvam", Credentials(allow_env=False))
    with pytest.raises(MissingCredential):
        p3.cred("api_key")


def test_shared_cred_groups():
    c = Credentials({"azure_speech": {"key": "k" * 10, "region": "eastus"}, "aws": {"access_key_id": "AK"}})
    assert get_provider("stt", "azure", c).cred("region") == "eastus"
    assert get_provider("tts", "azure", c).cred("key") == "k" * 10
    assert get_provider("llm", "bedrock", c).cred("access_key_id") == "AK"
    assert get_provider("tts", "aws", c).cred("region") == "ap-south-1"  # default


def test_credentials_repr_hides_values():
    c = Credentials({"openai": {"api_key": "sk-verysecretvalue"}})
    assert "verysecret" not in repr(c)
