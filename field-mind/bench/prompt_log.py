"""
Prompt recorder for the multi-agent Phase 1 gate ("every prompt sent to the
backend is byte-identical and sent in the same order").

RecordingBackend wraps any LLMBackend and appends one JSON line per
generate() call -- repair calls included -- with the episode and tick the
harness set, the role, max_tokens, the full prompt, and a sha256 of the
canonical mock_hint (the mock backend's answer depends on it, so an identical
prompt with a different hint would still be a different call).

Host-side tooling: lives in bench/, never imported by fieldmind/.
"""

from __future__ import annotations

import hashlib
import json


def _hint_hash(hint) -> str:
    return hashlib.sha256(json.dumps(hint, sort_keys=True, default=str)
                          .encode()).hexdigest()


class RecordingBackend:
    """Transparent wrapper: generate() is forwarded unchanged, the call logged."""

    def __init__(self, inner, path: str):
        self.inner = inner
        self.name = getattr(inner, "name", "recording")
        self._fh = open(path, "a")
        self.episode = ""
        self.tick = None

    def generate(self, prompt, role="generic", max_tokens=512, mock_hint=None):
        self._fh.write(json.dumps({
            "episode": self.episode, "tick": self.tick, "role": role,
            "max_tokens": max_tokens, "hint_sha256": _hint_hash(mock_hint),
            "prompt": prompt}) + "\n")
        return self.inner.generate(prompt, role=role, max_tokens=max_tokens,
                                   mock_hint=mock_hint)

    def close(self) -> None:
        self._fh.close()
        self.inner.close()
