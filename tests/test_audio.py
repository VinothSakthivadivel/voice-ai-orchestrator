import numpy as np

from app.audio.denoise import NoiseReducer
from app.audio.speaker import SpeakerGate, cosine, filter_words_by_speaker, MFCCEmbedder
from app.audio.vad import EnergyVAD, TurnDetector, frame_db
from tests.conftest import frames, silence, voice


def test_frame_db():
    assert frame_db(silence(0.02, level=0)) < -90
    assert frame_db(voice(0.02)) > -30


def test_vad_detects_voice_and_adapts_floor():
    vad = EnergyVAD()
    sil = [vad.is_speech(f) for f in frames(silence(1.0, level=0.003))]
    assert sum(sil) <= 2
    sp = [vad.is_speech(f) for f in frames(voice(0.5))]
    assert sum(sp) / len(sp) > 0.9
    assert vad.noise_floor < -35


def test_vad_rejects_steady_background_noise():
    vad = EnergyVAD(margin_db=10)
    noise = silence(2.0, level=0.02, seed=3)  # loud fan-like noise
    res = [vad.is_speech(f) for f in frames(noise)]
    assert sum(res[-50:]) <= 3  # adapted to the noise floor


def _run_turn(td, vad, pcm):
    evs = []
    for f in frames(pcm):
        evs += td.feed(f, vad.is_speech(f))
    return evs


def test_turn_detection_start_and_end():
    vad, td = EnergyVAD(), TurnDetector(min_speech_ms=200, end_silence_ms=500)
    evs = _run_turn(td, vad, silence(0.5) + voice(1.0) + silence(0.8))
    kinds = [e.kind for e in evs]
    assert kinds == ["speech_start", "speech_end"]
    end = evs[-1]
    assert 0.9 <= len(end.audio) / 32000 <= 2.0  # includes preroll + trailing silence
    assert end.speech_ms >= 900


def test_short_blip_is_not_a_turn():
    vad, td = EnergyVAD(), TurnDetector(min_speech_ms=200)
    evs = _run_turn(td, vad, silence(0.5) + voice(0.1) + silence(0.5))
    assert evs == []


def test_pause_inside_turn_does_not_end_it():
    vad, td = EnergyVAD(), TurnDetector(end_silence_ms=700)
    evs = _run_turn(td, vad, silence(0.4) + voice(0.6) + silence(0.3) + voice(0.6) + silence(1.0))
    assert [e.kind for e in evs] == ["speech_start", "speech_end"]


def test_max_utterance_cut():
    vad, td = EnergyVAD(), TurnDetector(max_utterance_ms=1000)
    evs = _run_turn(td, vad, silence(0.3) + voice(2.5))
    assert [e.kind for e in evs].count("speech_end") >= 1


def test_noise_reduction_attenuates_noise_keeps_voice():
    nr = NoiseReducer()
    for f in frames(silence(1.0, level=0.03, seed=5)):
        nr.add_noise_frame(f)
    n = np.frombuffer(silence(1.0, level=0.03, seed=6), "<i2").astype(float)
    on = np.frombuffer(nr.reduce(n.astype("<i2").tobytes()), "<i2").astype(float)
    assert len(on) == len(n)
    assert 10 * np.log10(np.mean(n ** 2) / np.mean(on ** 2)) > 10  # >10 dB noise attenuation
    a = np.frombuffer(voice(1.0), "<i2").astype(float)
    oa = np.frombuffer(nr.reduce(a.astype("<i2").tobytes()), "<i2").astype(float)
    assert np.corrcoef(oa, a)[0, 1] > 0.85  # speech structure preserved
    assert 10 * np.log10(np.mean(oa ** 2) / np.mean(a ** 2)) > -1.5  # speech energy kept


def test_speaker_embedding_discriminates():
    emb = MFCCEmbedder()
    a1 = emb.embed(voice(2.0, f0=110, formants=(600, 1100, 2500), seed=1))
    a2 = emb.embed(voice(2.0, f0=115, formants=(620, 1080, 2450), seed=2))
    b = emb.embed(voice(2.0, f0=240, formants=(900, 2000, 3200), seed=3))
    assert cosine(a1, a2) > cosine(a1, b)


def test_speaker_gate_enroll_then_reject_other_voice():
    g = SpeakerGate(enabled=True, threshold=0.0, enroll_seconds=3.0)
    me = lambda s: voice(1.6, f0=110, formants=(600, 1100, 2500), seed=s)
    other = voice(1.6, f0=250, formants=(950, 2100, 3300), seed=99)
    assert g.check(me(1))[0] and g.check(me(2))[0]
    assert g.enrolled
    _, sim_me = g.check(me(3))
    _, sim_other = g.check(other)
    assert sim_me > sim_other
    g.threshold = (sim_me + sim_other) / 2
    assert g.check(me(4))[0] is True
    assert g.check(other)[0] is False


def test_speaker_gate_disabled_accepts_all():
    assert SpeakerGate(enabled=False).check(voice(1.0)) == (True, None)


def test_diarization_filter():
    words = [{"word": "hello", "speaker": 0}, {"word": "noise", "speaker": 1}, {"word": "world", "speaker": 0}]
    text, primary = filter_words_by_speaker(words, None)
    assert text == "hello world" and primary == 0
    text, _ = filter_words_by_speaker(words, 1)
    assert text == "noise"
