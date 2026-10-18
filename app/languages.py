"""Indian language catalog and per-provider language / voice code mapping.

Every language is keyed by a canonical BCP-47-ish code (e.g. ``hi-IN``).  Each
provider has its own code for the same language (Sarvam uses ``od-IN`` for
Odia, Whisper uses ISO-639-1 ``hi``, Google uses ``pa-Guru-IN`` for Punjabi
STT ...).  ``provider_code`` resolves the canonical code into the provider's
code or returns ``None`` when the provider does not support it.

Support data reflects public provider documentation at time of writing; it is
deliberately conservative.  Providers add languages frequently - edit the maps
below (or pass a raw provider code in the bot config) to override.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Language:
    code: str
    name: str
    native: str
    script: str
    scheduled: bool = True  # in the 8th schedule of the Indian constitution


LANGUAGES: list[Language] = [
    Language("hi-IN", "Hindi", "हिन्दी", "Devanagari"),
    Language("bn-IN", "Bengali", "বাংলা", "Bengali"),
    Language("ta-IN", "Tamil", "தமிழ்", "Tamil"),
    Language("te-IN", "Telugu", "తెలుగు", "Telugu"),
    Language("mr-IN", "Marathi", "मराठी", "Devanagari"),
    Language("gu-IN", "Gujarati", "ગુજરાતી", "Gujarati"),
    Language("kn-IN", "Kannada", "ಕನ್ನಡ", "Kannada"),
    Language("ml-IN", "Malayalam", "മലയാളം", "Malayalam"),
    Language("or-IN", "Odia", "ଓଡ଼ିଆ", "Odia"),
    Language("pa-IN", "Punjabi", "ਪੰਜਾਬੀ", "Gurmukhi"),
    Language("as-IN", "Assamese", "অসমীয়া", "Bengali-Assamese"),
    Language("ur-IN", "Urdu", "اردو", "Perso-Arabic"),
    Language("en-IN", "English (India)", "English", "Latin", scheduled=False),
    Language("kok-IN", "Konkani", "कोंकणी", "Devanagari"),
    Language("mai-IN", "Maithili", "मैथिली", "Devanagari"),
    Language("sa-IN", "Sanskrit", "संस्कृतम्", "Devanagari"),
    Language("ne-IN", "Nepali", "नेपाली", "Devanagari"),
    Language("sd-IN", "Sindhi", "سنڌي", "Perso-Arabic / Devanagari"),
    Language("ks-IN", "Kashmiri", "کٲشُر", "Perso-Arabic"),
    Language("doi-IN", "Dogri", "डोगरी", "Devanagari"),
    Language("mni-IN", "Manipuri (Meitei)", "ꯃꯤꯇꯩꯂꯣꯟ", "Meitei / Bengali"),
    Language("brx-IN", "Bodo", "बड़ो", "Devanagari"),
    Language("sat-IN", "Santali", "ᱥᱟᱱᱛᱟᱲᱤ", "Ol Chiki"),
]
LANG_BY_CODE = {l.code: l for l in LANGUAGES}
ALL_CODES = [l.code for l in LANGUAGES]

_CORE11 = ["hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "gu-IN", "kn-IN", "ml-IN", "or-IN", "pa-IN", "en-IN"]


def _iso(codes: list[str]) -> dict[str, str]:
    """Map canonical codes to bare ISO-639 codes (hi-IN -> hi)."""
    return {c: c.split("-")[0] for c in codes}


def _same(codes: list[str]) -> dict[str, str]:
    return {c: c for c in codes}


# layer -> provider -> {canonical: provider_code}
STT_LANGS: dict[str, dict[str, str]] = {
    # Saaras v3/v4: 22 scheduled Indian languages + English
    "sarvam": {**_same([c for c in ALL_CODES]), "or-IN": "od-IN"},
    "google": {
        **_same(["hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "gu-IN", "kn-IN", "ml-IN",
                 "or-IN", "as-IN", "ur-IN", "en-IN"]),
        "pa-IN": "pa-Guru-IN", "ne-IN": "ne-NP",
    },
    "azure": {
        **_same(["hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "gu-IN", "kn-IN", "ml-IN",
                 "or-IN", "pa-IN", "as-IN", "ur-IN", "en-IN"]),
        "ne-IN": "ne-NP",
    },
    "aws": _same(["hi-IN", "en-IN"]),
    # nova-3 monolingual Indic support (2026): hi, bn, ta, te, mr, kn, gu, pa, as, ne, ur
    "deepgram": {**_iso(["hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "kn-IN", "gu-IN", "pa-IN", "as-IN",
                         "ne-IN", "ur-IN"]), "en-IN": "en-IN"},
    "openai": _iso(["hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "gu-IN", "kn-IN", "ml-IN",
                    "pa-IN", "as-IN", "ur-IN", "en-IN", "ne-IN", "sa-IN", "sd-IN"]),
    "assemblyai": _iso(["hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "gu-IN", "kn-IN", "ml-IN",
                        "pa-IN", "ur-IN", "en-IN", "ne-IN"]),
    "mock": _same(ALL_CODES),
}

TTS_LANGS: dict[str, dict[str, str]] = {
    "sarvam": {**_same(_CORE11), "or-IN": "od-IN"},
    "google": _same(["hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "gu-IN", "kn-IN", "ml-IN",
                     "pa-IN", "ur-IN", "en-IN"]),
    "azure": {
        **_same(["hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "gu-IN", "kn-IN", "ml-IN",
                 "or-IN", "pa-IN", "as-IN", "ur-IN", "en-IN"]),
        "ne-IN": "ne-NP",
    },
    "aws": _same(["hi-IN", "en-IN"]),
    "elevenlabs": _iso(["hi-IN", "ta-IN", "en-IN"]),
    "openai": _iso(["hi-IN", "mr-IN", "ta-IN", "ur-IN", "kn-IN", "ne-IN", "en-IN"]),
    # free: Edge neural voices (hi,bn,ta,te,mr,gu,kn,ml,ur,en,ne) + gTTS (pa); others via browser speech
    "free": _same(["hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "gu-IN", "kn-IN", "ml-IN", "ur-IN", "en-IN",
                   "ne-IN", "pa-IN"]),
    "browser": _same(ALL_CODES),  # depends on the user's device voices
    "mock": _same(ALL_CODES),
}

# LLMs are text models: every listed language "works", quality varies a lot
# (strong for Hindi/English, weaker for low-resource languages).
LLM_PROVIDERS_ALL_LANGS = True

# Default voices: provider -> canonical lang -> voice id/name
AZURE_VOICES: dict[str, list[str]] = {
    "hi-IN": ["hi-IN-SwaraNeural", "hi-IN-MadhurNeural"],
    "bn-IN": ["bn-IN-TanishaaNeural", "bn-IN-BashkarNeural"],
    "ta-IN": ["ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"],
    "te-IN": ["te-IN-ShrutiNeural", "te-IN-MohanNeural"],
    "mr-IN": ["mr-IN-AarohiNeural", "mr-IN-ManoharNeural"],
    "gu-IN": ["gu-IN-DhwaniNeural", "gu-IN-NiranjanNeural"],
    "kn-IN": ["kn-IN-SapnaNeural", "kn-IN-GaganNeural"],
    "ml-IN": ["ml-IN-SobhanaNeural", "ml-IN-MidhunNeural"],
    "or-IN": ["or-IN-SubhasiniNeural", "or-IN-SukantNeural"],
    "pa-IN": ["pa-IN-VaaniNeural", "pa-IN-OjasNeural"],
    "as-IN": ["as-IN-YashicaNeural", "as-IN-PriyomNeural"],
    "ur-IN": ["ur-IN-GulNeural", "ur-IN-SalmanNeural"],
    "en-IN": ["en-IN-NeerjaNeural", "en-IN-PrabhatNeural"],
    "ne-IN": ["ne-NP-HemkalaNeural", "ne-NP-SagarNeural"],
}
# bulbul:v3 speakers (v2 legacy: anushka, manisha, vidya, arya, abhilash, karun, hitesh;
# bulbul:v4-flash uses persona ids such as shubh_hi_customer - type any id in the voice field)
SARVAM_SPEAKERS = ["shubh", "ritu", "priya", "neha", "rahul", "pooja", "rohan", "simran", "kavya", "amit", "dev",
                   "ishita", "shreya", "aditya", "kabir", "anand", "tanya", "vijay", "kavitha", "rupali"]
POLLY_VOICES = {"hi-IN": ["Kajal", "Aditi"], "en-IN": ["Kajal", "Raveena", "Aditi"]}
OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"]
GOOGLE_VOICE_HINT = "Leave blank to let Google pick (e.g. hi-IN-Neural2-A, ta-IN-Wavenet-A)"
ELEVENLABS_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"

VOICES = {
    "azure": AZURE_VOICES,
    "sarvam": {c: SARVAM_SPEAKERS for c in TTS_LANGS["sarvam"]},
    "aws": POLLY_VOICES,
    "openai": {c: OPENAI_VOICES for c in TTS_LANGS["openai"]},
    "elevenlabs": {c: [ELEVENLABS_DEFAULT_VOICE] for c in TTS_LANGS["elevenlabs"]},
    "google": {c: [] for c in TTS_LANGS["google"]},
    "free": {**{c: v for c, v in AZURE_VOICES.items() if c not in ("or-IN", "pa-IN", "as-IN")}, "pa-IN": []},
    "browser": {c: [] for c in ALL_CODES},
    "mock": {c: ["tone"] for c in ALL_CODES},
}


def provider_code(layer: str, provider: str, lang: str) -> str | None:
    """Resolve canonical ``lang`` to provider code. ``auto`` passes through.

    LLM layer always returns the canonical code (all languages accepted).
    """
    if lang in (None, "", "auto"):
        return "auto"
    if layer == "llm":
        return lang
    table = {"stt": STT_LANGS, "tts": TTS_LANGS}[layer]
    return table.get(provider, {}).get(lang)


def supports(layer: str, provider: str, lang: str) -> bool:
    return provider_code(layer, provider, lang) is not None


def default_voice(provider: str, lang: str) -> str | None:
    voices = VOICES.get(provider, {}).get(lang) or []
    return voices[0] if voices else None


def support_matrix(stt: list[str], tts: list[str], llm: list[str]) -> dict:
    rows = []
    for l in LANGUAGES:
        rows.append({
            **asdict(l),
            "stt": [p for p in stt if supports("stt", p, l.code)],
            "tts": [p for p in tts if supports("tts", p, l.code)],
            "llm": list(llm),
            "voices": {p: VOICES.get(p, {}).get(l.code, []) for p in tts if supports("tts", p, l.code)},
        })
    return {"languages": rows}
