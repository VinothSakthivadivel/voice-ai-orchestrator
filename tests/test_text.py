from app.analysis.sentiment import analyze_lexicon
from app.pipeline.chunker import SentenceChunker


def test_chunker_splits_sentences_incl_danda():
    c = SentenceChunker()
    out = []
    for tok in "नमस्ते, आप कैसे हैं? मैं ठीक हूँ। Thank you very much. ok".split(" "):
        out += c.feed(tok + " ")
    out += c.flush()
    assert out == ["नमस्ते, आप कैसे हैं?", "मैं ठीक हूँ।", "Thank you very much.", "ok"]


def test_chunker_cuts_long_clause():
    c = SentenceChunker(soft_max=40)
    out = c.feed("this is a very long clause, without a full stop that goes on and on and on ")
    assert out and all(len(x) <= 60 for x in out)


def test_sentiment_english():
    assert analyze_lexicon("This is great, thank you so much!").label == "positive"
    s = analyze_lexicon("This is the worst service, I am very angry")
    assert s.label == "negative" and s.emotion == "anger"
    assert analyze_lexicon("What time is it").label == "neutral"


def test_sentiment_negation():
    assert analyze_lexicon("this is not good").score < 0


def test_sentiment_indic():
    assert analyze_lexicon("बहुत बढ़िया, धन्यवाद").label == "positive"
    assert analyze_lexicon("यह बहुत बेकार है, मुझे गुस्सा आ रहा है").label == "negative"
    assert analyze_lexicon("अच्छा नहीं है").score < 0
    assert analyze_lexicon("ரொம்ப நன்றி").label == "positive"
    assert analyze_lexicon("bahut bekar service hai").label == "negative"
