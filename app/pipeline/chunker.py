"""Split a streaming LLM response into speakable chunks (sentence-ish) for low-latency TTS."""
from __future__ import annotations

import re

_END = re.compile(r"([.!?।॥۔؟\n]+)(\s+|$)")
_SOFT = re.compile(r"([,;:،]+)(\s+)")


class SentenceChunker:
    def __init__(self, first_min: int = 12, soft_max: int = 120):
        self.buf = ""
        self.first_min = first_min
        self.soft_max = soft_max
        self.emitted = 0

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out = []
        while True:
            m = _END.search(self.buf)
            if m and (m.end(1) >= self.first_min or self.emitted):
                chunk, self.buf = self.buf[:m.end(1)].strip(), self.buf[m.end():]
                if chunk:
                    out.append(chunk); self.emitted += 1
                continue
            if len(self.buf) > self.soft_max:  # long clause without full stop: cut at a comma/space
                m2 = None
                for m2 in _SOFT.finditer(self.buf):
                    pass
                cut = m2.end(1) if m2 else self.buf.rfind(" ", 0, self.soft_max)
                if cut and cut > 0:
                    chunk, self.buf = self.buf[:cut].strip(), self.buf[cut:].lstrip()
                    if chunk:
                        out.append(chunk); self.emitted += 1
                    continue
            break
        return out

    def flush(self) -> list[str]:
        c, self.buf = self.buf.strip(), ""
        return [c] if c else []
