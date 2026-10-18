"""Shared credential definitions (one key set reused by STT/LLM/TTS of a vendor)."""
from .base import CredField as F

SARVAM = [F("api_key", "SARVAM_API_KEY", "Sarvam API subscription key")]
GOOGLE_CLOUD = [F("api_key", "GOOGLE_CLOUD_API_KEY", "Google Cloud API key (Speech-to-Text / Text-to-Speech enabled)")]
AZURE_SPEECH = [
    F("key", "AZURE_SPEECH_KEY", "Azure Speech resource key"),
    F("region", "AZURE_SPEECH_REGION", "Azure region (e.g. centralindia)", secret=False, default="centralindia"),
]
AWS = [
    F("access_key_id", "AWS_ACCESS_KEY_ID", "AWS access key id"),
    F("secret_access_key", "AWS_SECRET_ACCESS_KEY", "AWS secret access key"),
    F("session_token", "AWS_SESSION_TOKEN", "AWS session token (optional)", required=False),
    F("region", "AWS_REGION", "AWS region", secret=False, default="ap-south-1"),
]
DEEPGRAM = [F("api_key", "DEEPGRAM_API_KEY", "Deepgram API key")]
OPENAI = [
    F("api_key", "OPENAI_API_KEY", "OpenAI API key"),
    F("base_url", "OPENAI_BASE_URL", "Base URL (optional)", secret=False, required=False,
      default="https://api.openai.com/v1"),
]
ASSEMBLYAI = [F("api_key", "ASSEMBLYAI_API_KEY", "AssemblyAI API key")]
ELEVENLABS = [F("api_key", "ELEVENLABS_API_KEY", "ElevenLabs API key")]
ANTHROPIC = [F("api_key", "ANTHROPIC_API_KEY", "Anthropic API key")]
GEMINI = [F("api_key", "GEMINI_API_KEY", "Google AI Studio (Gemini) API key")]
AZURE_OPENAI = [
    F("api_key", "AZURE_OPENAI_API_KEY", "Azure OpenAI key"),
    F("endpoint", "AZURE_OPENAI_ENDPOINT", "Endpoint (https://<name>.openai.azure.com)", secret=False),
    F("deployment", "AZURE_OPENAI_DEPLOYMENT", "Deployment name", secret=False),
    F("api_version", "AZURE_OPENAI_API_VERSION", "API version", secret=False, required=False,
      default="2024-10-21"),
]
GROQ = [F("api_key", "GROQ_API_KEY", "Groq API key")]
MISTRAL = [F("api_key", "MISTRAL_API_KEY", "Mistral API key")]
OPENROUTER = [F("api_key", "OPENROUTER_API_KEY", "OpenRouter API key")]
OPENAI_COMPAT = [
    F("api_key", "OPENAI_COMPAT_API_KEY", "API key", required=False),
    F("base_url", "OPENAI_COMPAT_BASE_URL", "Base URL (e.g. http://host:8000/v1 for vLLM/Ollama/Together)",
      secret=False),
]
