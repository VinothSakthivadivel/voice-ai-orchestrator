"""Normalise every provider's TTS output to one wire format: PCM16 little-endian mono @ declared rate.

Handles: WAV/RIFF (PCM 8/16/24/32-bit int, IEEE float32/64, WAVE_FORMAT_EXTENSIBLE, multi-channel,
streaming WAVs with bogus 0xFFFFFFFF sizes), raw PCM16 (odd trailing byte trimmed), and MP3/OGG/FLAC
(decoded with ``miniaudio`` when installed; otherwise passed through for the browser's decodeAudioData).
The declared format is not trusted blindly: payloads are sniffed (RIFF / ID3 / MPEG sync / OggS / fLaC).
"""
from __future__ import annotations

import struct

import numpy as np

try:  # optional native decoder (wheels for linux/mac/win)
    import miniaudio  # type: ignore
except Exception:  # pragma: no cover
    miniaudio = None

from ..providers.base import AudioChunk


class AudioFormatError(ValueError):
    pass


def sniff(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"fLaC":
        return "flac"
    return None


def _to_mono_int16(x: np.ndarray, channels: int) -> np.ndarray:
    if channels > 1:
        x = x[: len(x) - len(x) % channels].reshape(-1, channels).mean(axis=1)
    return np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2")


def parse_wav(data: bytes) -> tuple[bytes, int]:
    """Robust RIFF/WAVE parser -> (pcm16 mono bytes, sample_rate)."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioFormatError("not a RIFF/WAVE payload")
    pos, fmt = 12, None
    audio = None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body_start = pos + 8
        if cid == b"fmt ":
            tag, ch, rate, _br, _align, bits = struct.unpack("<HHIIHH", data[body_start:body_start + 16])
            if tag == 0xFFFE and size >= 40:  # WAVE_FORMAT_EXTENSIBLE: real tag in sub-format GUID
                tag = struct.unpack("<H", data[body_start + 24:body_start + 26])[0]
            fmt = (tag, ch, rate, bits)
        elif cid == b"data":
            end = len(data) if size in (0, 0xFFFFFFFF) or body_start + size > len(data) else body_start + size
            audio = data[body_start:end]
            break
        pos = body_start + size + (size & 1)
    if fmt is None or audio is None:
        raise AudioFormatError("WAV missing fmt or data chunk")
    tag, ch, rate, bits = fmt
    ch = max(ch, 1)
    if tag == 1:  # integer PCM
        if bits == 16:
            x = np.frombuffer(audio[: len(audio) - len(audio) % 2], "<i2").astype(np.float32) / 32768
        elif bits == 8:
            x = (np.frombuffer(audio, np.uint8).astype(np.float32) - 128) / 128
        elif bits == 24:
            a = np.frombuffer(audio[: len(audio) - len(audio) % 3], np.uint8).reshape(-1, 3).astype(np.int32)
            v = a[:, 0] | (a[:, 1] << 8) | (a[:, 2] << 16)
            v = np.where(v & 0x800000, v - 0x1000000, v)
            x = v.astype(np.float32) / 8388608
        elif bits == 32:
            x = np.frombuffer(audio[: len(audio) - len(audio) % 4], "<i4").astype(np.float32) / 2147483648
        else:
            raise AudioFormatError(f"unsupported PCM bit depth {bits}")
    elif tag == 3:  # IEEE float
        dt = "<f4" if bits == 32 else "<f8"
        n = 4 if bits == 32 else 8
        x = np.frombuffer(audio[: len(audio) - len(audio) % n], dt).astype(np.float32)
    else:
        raise AudioFormatError(f"unsupported WAV format tag {tag} (e.g. mu-law/ADPCM)")
    if ch == 1 and tag == 1 and bits == 16:
        pcm = audio[: len(audio) - len(audio) % 2]  # fast path, already PCM16 LE mono
    else:
        pcm = _to_mono_int16(x, ch).tobytes()
    return pcm, rate


def decode_compressed(data: bytes) -> tuple[bytes, int] | None:
    if miniaudio is None:
        return None
    try:
        getter = {"mp3": "mp3_get_info", "ogg": "vorbis_get_info", "flac": "flac_get_info"}.get(sniff(data) or "")
        native = getattr(miniaudio, getter)(data).sample_rate if getter else 0
    except Exception:
        native = 0
    try:  # decode at the stream's native rate (miniaudio otherwise resamples to 44.1 kHz)
        info = miniaudio.decode(data, output_format=miniaudio.SampleFormat.SIGNED16, nchannels=1,
                                sample_rate=native or 24000)
        return info.samples.tobytes(), info.sample_rate
    except Exception:
        return None


def normalize(chunk: AudioChunk) -> AudioChunk:
    """Return chunk as format 'pcm16' (mono, LE) or, if undecodable server-side, a browser-decodable
    compressed format ('mp3'/'ogg'/'flac'). 'speech' (browser speechSynthesis text) passes through."""
    if chunk.format == "speech":
        return chunk
    data = chunk.data or b""
    kind = sniff(data) or chunk.format
    if kind == "wav":
        pcm, rate = parse_wav(data)
        return AudioChunk(pcm, "pcm16", rate)
    if kind in ("mp3", "ogg", "flac"):
        dec = decode_compressed(data)
        if dec:
            return AudioChunk(dec[0], "pcm16", dec[1])
        return AudioChunk(data, kind, chunk.sample_rate)
    if kind == "pcm16":
        return AudioChunk(data[: len(data) - len(data) % 2], "pcm16", chunk.sample_rate or 16000)
    raise AudioFormatError(f"unknown audio format {chunk.format!r}")


def duration_s(chunk: AudioChunk) -> float:
    if chunk.format == "pcm16":
        return len(chunk.data) / 2 / max(chunk.sample_rate, 1)
    if chunk.format == "speech":
        return 0.075 * len(chunk.data.decode("utf-8", "ignore")) + 0.3
    return len(chunk.data) / 6000  # ~48 kbps compressed
