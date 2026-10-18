"""Feed real-format TTS payloads through the server-side audio normaliser."""
import io
import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from app.audio import codec
from app.audio.codec import AudioFormatError, normalize, parse_wav, sniff
from app.providers.base import AudioChunk

FIX = Path(__file__).parent / "fixtures"


def tone(sr=22050, sec=0.3, f=300.0):
    t = np.arange(int(sr * sec)) / sr
    return 0.5 * np.sin(2 * np.pi * f * t)


def wav_bytes(x, sr, bits=16, ch=1, fmt_tag=1, extensible=False, extra_chunk=False, streaming=False):
    if ch > 1:
        x = np.repeat(x[:, None], ch, axis=1).reshape(-1)
    if fmt_tag == 3:
        data = x.astype("<f4").tobytes(); bits = 32
    elif bits == 16:
        data = (x * 32767).astype("<i2").tobytes()
    elif bits == 24:
        v = (x * 8388607).astype("<i4")
        data = b"".join(int(s).to_bytes(4, "little", signed=True)[:3] for s in v)
    elif bits == 8:
        data = ((x * 127) + 128).astype(np.uint8).tobytes()
    align = ch * bits // 8
    if extensible:
        fmt = struct.pack("<HHIIHH", 0xFFFE, ch, sr, sr * align, align, bits)
        fmt += struct.pack("<HHI", 22, bits, 0) + struct.pack("<H", fmt_tag) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    else:
        fmt = struct.pack("<HHIIHH", fmt_tag, ch, sr, sr * align, align, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if extra_chunk:  # Azure/ffmpeg style LIST chunk before data (odd size -> pad byte)
        info = b"INFOISFT\x05\x00\x00\x00Lavf\x00"
        body += b"LIST" + struct.pack("<I", len(info)) + info + (b"\x00" if len(info) & 1 else b"")
    size = 0xFFFFFFFF if streaming else len(data)
    body += b"data" + struct.pack("<I", size) + data
    return b"RIFF" + struct.pack("<I", 0xFFFFFFFF if streaming else len(body)) + body


def samples(chunk):
    assert chunk.format == "pcm16"
    return np.frombuffer(chunk.data, "<i2").astype(float) / 32768


@pytest.mark.parametrize("kw", [
    {"bits": 16}, {"bits": 8}, {"bits": 24}, {"fmt_tag": 3}, {"bits": 16, "ch": 2},
    {"bits": 24, "extensible": True}, {"bits": 16, "extra_chunk": True}, {"bits": 16, "streaming": True},
])
def test_wav_variants_normalise_to_mono_pcm16(kw):
    x = tone(24000)
    out = normalize(AudioChunk(wav_bytes(x, 24000, **kw), "wav", 0))
    assert out.format == "pcm16" and out.sample_rate == 24000
    y = samples(out)
    assert abs(len(y) - len(x)) <= 1
    assert np.corrcoef(y[:len(x)], x[:len(y)])[0, 1] > 0.99  # same waveform, no header bytes played as audio


def test_wav_header_is_not_played_as_samples():
    x = tone(22050)
    out = normalize(AudioChunk(wav_bytes(x, 22050), "pcm16", 22050))  # mislabeled as raw PCM by a provider
    assert out.sample_rate == 22050 and len(out.data) == len(x) * 2  # header stripped via sniffing


def test_raw_pcm_odd_bytes_trimmed():
    out = normalize(AudioChunk(b"\x01\x00\x02\x00\x03", "pcm16", 16000))
    assert out.data == b"\x01\x00\x02\x00" and out.sample_rate == 16000


def test_python_wave_module_output_roundtrip():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x10\x00" * 800)
    pcm, rate = parse_wav(buf.getvalue())
    assert rate == 16000 and pcm == b"\x10\x00" * 800


def test_unsupported_wav_codec_raises():
    bad = wav_bytes(tone(8000), 8000, bits=8)
    bad = bad.replace(struct.pack("<HH", 1, 1), struct.pack("<HH", 7, 1), 1)  # mu-law tag
    with pytest.raises(AudioFormatError):
        parse_wav(bad)


def test_sniff():
    assert sniff(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "wav"
    assert sniff(b"ID3\x04") == "mp3" and sniff(b"\xff\xf3d\xc4") == "mp3"
    assert sniff(b"OggS") == "ogg" and sniff(b"\x00\x01") is None


@pytest.mark.skipif(codec.miniaudio is None, reason="miniaudio not installed")
def test_real_mp3_from_edge_tts_decodes_to_speech_like_pcm():
    out = normalize(AudioChunk((FIX / "edge_en_in.mp3").read_bytes(), "mp3", 24000))
    assert out.format == "pcm16" and out.sample_rate == 24000
    y = samples(out)
    assert 0.8 < len(y) / 24000 < 4.0
    assert speech_like(y, 24000)


def test_mp3_passthrough_without_decoder(monkeypatch):
    monkeypatch.setattr(codec, "miniaudio", None)
    data = (FIX / "edge_en_in.mp3").read_bytes()
    out = normalize(AudioChunk(data, "mp3", 24000))
    assert out.format == "mp3" and out.data == data  # browser decodeAudioData handles it


def test_speech_passthrough():
    c = AudioChunk("नमस्ते".encode(), "speech", 0)
    assert normalize(c) is c


def speech_like(y, sr):
    """Speech: energy varies a lot (syllables/pauses) and spectral centroid moves; tones/noise don't."""
    n = int(0.03 * sr)
    frames = np.stack([y[i:i + n] for i in range(0, len(y) - n, n)])
    e = np.sqrt((frames ** 2).mean(1)) + 1e-9
    active = frames[e > 0.1 * e.max()]
    spec = np.abs(np.fft.rfft(active * np.hanning(n), axis=1))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    cent = (spec * freqs).sum(1) / (spec.sum(1) + 1e-9)
    return e.std() / e.mean() > 0.5 and cent.std() > 150


def test_speech_detector_rejects_tone_and_noise():
    assert not speech_like(tone(24000, 2.0), 24000)
    rng = np.random.default_rng(0)
    assert not speech_like(rng.normal(0, 0.2, 48000), 24000)
