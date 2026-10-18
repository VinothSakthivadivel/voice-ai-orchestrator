"""Lightweight multilingual sentiment + emotion analysis of user turns.

``lexicon`` mode: zero-latency rule-based scoring with English, Hinglish
(romanised Hindi) and native-script cue words for the major Indian languages,
with negation and intensifier handling.  ``llm`` mode asks the configured LLM
for a JSON label and falls back to the lexicon on any failure.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

POS = {
    # english / hinglish
    "good", "great", "excellent", "awesome", "amazing", "nice", "thanks", "thank", "happy", "love", "perfect",
    "wonderful", "helpful", "glad", "fantastic", "resolved", "satisfied", "cool", "super", "best", "pleased",
    "accha", "achha", "acha", "badhiya", "badiya", "shukriya", "dhanyavad", "dhanyawad", "mast", "khush", "sahi",
    "romba", "nandri", "bagundi", "dhanyavadalu", "bhalo", "chhan", "chan",
    # native scripts
    "अच्छा", "अच्छी", "बढ़िया", "धन्यवाद", "शुक्रिया", "खुश", "बहुत बढ़िया", "शानदार", "छान", "आनंद",
    "நன்றி", "நல்ல", "சூப்பர்", "மகிழ்ச்சி", "ధన్యవాదాలు", "బాగుంది", "సంతోషం", "ধন্যবাদ", "ভালো", "খুশি",
    "ಧನ್ಯವಾದ", "ಒಳ್ಳೆಯ", "ಚೆನ್ನಾಗಿದೆ", "നന്ദി", "നല്ല", "സന്തോഷം", "આભાર", "સારું", "મજા", "ਧੰਨਵਾਦ", "ਵਧੀਆ",
    "ଧନ୍ୟବାଦ", "ଭଲ", "شکریہ", "اچھا", "خوش",
}
NEG = {
    "bad", "terrible", "worst", "awful", "hate", "angry", "upset", "problem", "issue", "broken", "useless",
    "poor", "disappointed", "frustrated", "annoying", "horrible", "wrong", "slow", "never", "refund", "complaint",
    "cancel", "sad", "unhappy", "worried", "scared", "ridiculous", "fraud", "cheated",
    "bekar", "bakwas", "bura", "kharab", "ganda", "gussa", "pareshan", "dukhi", "dhokha", "mosam", "kharap",
    "बुरा", "बेकार", "बकवास", "खराब", "गुस्सा", "परेशान", "दुखी", "धोखा", "शिकायत", "समस्या", "वाईट",
    "மோசம்", "பிரச்சனை", "கோபம்", "చెడు", "సమస్య", "కోపం", "খারাপ", "সমস্যা", "রাগ", "ಕೆಟ್ಟ", "ಸಮಸ್ಯೆ",
    "മോശം", "പ്രശ്നം", "ખરાબ", "સમસ્યા", "ਮਾੜਾ", "ਸਮੱਸਿਆ", "ଖରାପ", "برا", "مسئلہ", "غصہ",
}
EMOTIONS = {
    "anger": {"angry", "furious", "worst", "ridiculous", "hate", "fraud", "cheated", "nonsense", "gussa", "bakwas",
              "गुस्सा", "बकवास", "धोखा", "கோபம்", "కోపం", "রাগ", "غصہ"},
    "sadness": {"sad", "unhappy", "disappointed", "upset", "dukhi", "दुखी", "निराश", "துக்கம்", "బాధ", "দুঃখ"},
    "fear": {"worried", "scared", "afraid", "anxious", "urgent", "dar", "chinta", "डर", "चिंता", "பயம்", "భయం", "ভয়"},
    "joy": {"happy", "great", "awesome", "love", "glad", "wonderful", "khush", "mast", "खुश", "शानदार",
            "மகிழ்ச்சி", "సంతోషం", "খুশি", "സന്തോഷം"},
    "surprise": {"wow", "really", "unbelievable", "seriously", "arre", "अरे", "वाह", "sachi"},
    "gratitude": {"thanks", "thank", "shukriya", "dhanyavad", "धन्यवाद", "शुक्रिया", "நன்றி", "ధన్యవాదాలు",
                  "ধন্যবাদ", "ಧನ್ಯವಾದ", "നന്ദി", "આભાર", "ਧੰਨਵਾਦ", "ଧନ୍ୟବାଦ", "شکریہ"},
}
NEGATORS_LATIN = {"not", "no", "never", "dont", "don't", "isnt", "isn't", "nahi", "nahin", "mat", "illa", "ledu"}
NEGATORS_NATIVE = ["नहीं", "नही", "मत", "இல்லை", "లేదు", "না", "ಇಲ್ಲ", "ഇല്ല", "નથી", "ਨਹੀਂ", "ନାହିଁ", "نہیں"]
INTENSIFIERS = {"very", "so", "really", "extremely", "too", "bahut", "bohot", "bahot", "bilkul", "बहुत", "ரொம்ப",
                "చాలా", "খুব", "ತುಂಬಾ", "വളരെ", "ખૂબ", "ਬਹੁਤ"}
_TOKEN = re.compile(r"[a-z']+")


@dataclass
class Sentiment:
    score: float      # -1 (very negative) .. +1 (very positive)
    label: str        # negative | neutral | positive
    emotion: str      # anger | sadness | fear | joy | surprise | gratitude | neutral
    intensity: float  # 0..1
    method: str = "lexicon"

    def to_dict(self):
        return asdict(self)


def _hits(text: str, tokens: list[str], lex: set[str]) -> list[tuple[str, int]]:
    """Return (term, position) hits; position = token index (latin) or -1 (native substring)."""
    out = []
    for i, t in enumerate(tokens):
        if t in lex:
            out.append((t, i))
    for term in lex:
        if not term.isascii() and term in text:
            out.append((term, -1))
    return out


def _negated(text: str, tokens: list[str], term: str, pos: int) -> bool:
    if pos >= 0:
        return any(t in NEGATORS_LATIN for t in tokens[max(0, pos - 3):pos])
    idx = text.find(term)
    window = text[idx: idx + len(term) + 15]
    return any(n in window for n in NEGATORS_NATIVE)


def analyze_lexicon(text: str) -> Sentiment:
    low = text.lower()
    tokens = _TOKEN.findall(low)
    score = 0.0
    for term, pos in _hits(low, tokens, POS):
        score += -1.0 if _negated(low, tokens, term, pos) else 1.0
    for term, pos in _hits(low, tokens, NEG):
        score += 0.5 if _negated(low, tokens, term, pos) else -1.0
    boost = 1.0 + 0.5 * sum(1 for t in tokens if t in INTENSIFIERS) + 0.5 * sum(1 for i in INTENSIFIERS
                                                                                if not i.isascii() and i in low)
    boost += 0.3 * min(text.count("!"), 3)
    raw = score * boost
    norm = max(-1.0, min(1.0, raw / 3.0))
    emo_scores = {e: len(_hits(low, tokens, words)) for e, words in EMOTIONS.items()}
    emotion = max(emo_scores, key=emo_scores.get) if any(emo_scores.values()) else "neutral"
    if emotion in ("joy", "gratitude") and norm < 0:
        emotion = "neutral"
    label = "positive" if norm > 0.15 else "negative" if norm < -0.15 else "neutral"
    return Sentiment(round(norm, 3), label, emotion, round(min(1.0, abs(raw) / 3.0), 3))


LLM_PROMPT = ("Classify the user's utterance (any Indian language or English). Respond ONLY with JSON: "
              '{"score": <-1..1>, "label": "negative|neutral|positive", '
              '"emotion": "anger|sadness|fear|joy|surprise|gratitude|neutral", "intensity": <0..1>}')


async def analyze_llm(text: str, llm, model: str | None) -> Sentiment:
    try:
        out = await llm.complete([{"role": "system", "content": LLM_PROMPT}, {"role": "user", "content": text}],
                                 model=model, temperature=0.0, max_tokens=80, options={})
        m = re.search(r"\{.*\}", out, re.S)
        j = json.loads(m.group(0)) if m else {}
        return Sentiment(float(j.get("score", 0)), str(j.get("label", "neutral")), str(j.get("emotion", "neutral")),
                         float(j.get("intensity", 0)), method="llm")
    except Exception:
        s = analyze_lexicon(text)
        s.method = "lexicon(fallback)"
        return s
