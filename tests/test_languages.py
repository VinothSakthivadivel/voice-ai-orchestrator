from app.languages import (ALL_CODES, LANGUAGES, STT_LANGS, TTS_LANGS, default_voice, provider_code,
                           support_matrix, supports)

REQUIRED = ["hi-IN", "bn-IN", "ta-IN", "te-IN", "mr-IN", "gu-IN", "kn-IN", "ml-IN", "or-IN", "pa-IN", "as-IN",
            "ur-IN", "en-IN", "kok-IN", "mai-IN", "sa-IN", "ne-IN", "sd-IN", "ks-IN", "doi-IN", "mni-IN", "brx-IN",
            "sat-IN"]


def test_catalog_complete():
    assert set(REQUIRED) <= set(ALL_CODES)
    assert len({l.code for l in LANGUAGES}) == len(LANGUAGES)


def test_provider_specific_codes():
    assert provider_code("stt", "sarvam", "or-IN") == "od-IN"
    assert provider_code("tts", "sarvam", "or-IN") == "od-IN"
    assert provider_code("stt", "google", "pa-IN") == "pa-Guru-IN"
    assert provider_code("stt", "azure", "ne-IN") == "ne-NP"
    assert provider_code("stt", "openai", "ta-IN") == "ta"
    assert provider_code("stt", "deepgram", "hi-IN") == "hi"
    assert provider_code("tts", "elevenlabs", "hi-IN") == "hi"


def test_unsupported_returns_none():
    assert provider_code("tts", "aws", "ta-IN") is None
    assert not supports("tts", "google", "sat-IN")
    assert provider_code("stt", "sarvam", "auto") == "auto"
    assert provider_code("llm", "anything", "sat-IN") == "sat-IN"


def test_every_core_indic_language_has_stt_and_tts():
    core = REQUIRED[:13]
    for c in core:
        assert any(c in m for p, m in STT_LANGS.items() if p != "mock"), c
        assert any(c in m for p, m in TTS_LANGS.items() if p != "mock"), c


def test_voices_and_matrix():
    assert default_voice("azure", "ta-IN") == "ta-IN-PallaviNeural"
    assert default_voice("sarvam", "hi-IN") == "shubh"
    assert provider_code("stt", "sarvam", "sat-IN") == "sat-IN"  # Saaras v4: all 22 scheduled languages
    assert provider_code("stt", "deepgram", "ta-IN") == "ta"
    m = support_matrix(["sarvam", "azure"], ["sarvam", "azure"], ["openai"])
    hi = next(r for r in m["languages"] if r["code"] == "hi-IN")
    assert set(hi["stt"]) == {"sarvam", "azure"} and hi["llm"] == ["openai"]
    assert "hi-IN-SwaraNeural" in hi["voices"]["azure"]
