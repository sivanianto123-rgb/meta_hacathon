"""Client for Meta's Model API (Muse Spark for text, Muse Voice Transcribe for audio).

Configuration comes from the environment so no key ever lands in the repo:

    META_API_KEY            required
    META_API_BASE           API base URL (default: https://api.meta.ai/v1)
    META_MODEL              chat model id (default: muse-spark-1.3)
    META_TRANSCRIBE_MODEL   transcription model id (default: muse-voice-transcribe-1.0)

The Meta Model API is OpenAI SDK compatible, so requests use the chat/completions and
audio/transcriptions shapes. Models and prices: https://dev.meta.ai/products/meta-model-api
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests


DEFAULT_BASE = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
DEFAULT_TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"


class LLMNotConfigured(RuntimeError):
    pass


class LLMError(RuntimeError):
    pass


class ChatModel(Protocol):
    def complete(self, system: str, user: str) -> str: ...


@dataclass
class MetaClient:
    api_key: str
    base_url: str
    model: str
    transcribe_model: str | None = None
    timeout: float = 90.0

    @classmethod
    def from_env(cls) -> "MetaClient":
        if not os.environ.get("META_API_KEY"):
            raise LLMNotConfigured("Set META_API_KEY to use the Meta Model API")
        return cls(
            api_key=os.environ["META_API_KEY"],
            base_url=os.environ.get("META_API_BASE", DEFAULT_BASE).rstrip("/"),
            model=os.environ.get("META_MODEL", DEFAULT_MODEL),
            transcribe_model=os.environ.get("META_TRANSCRIBE_MODEL", DEFAULT_TRANSCRIBE_MODEL),
        )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def complete(self, system: str, user: str) -> str:
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": self.model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise LLMError(f"Meta API returned {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"Unexpected response shape: {json.dumps(data)[:500]}") from e

    def transcribe(self, audio_path: str | Path) -> str:
        if not self.transcribe_model:
            raise LLMNotConfigured("Set META_TRANSCRIBE_MODEL to transcribe audio")
        with open(audio_path, "rb") as f:
            resp = requests.post(
                f"{self.base_url}/audio/transcriptions",
                headers=self._headers(),
                data={"model": self.transcribe_model},
                files={"file": (Path(audio_path).name, f)},
                timeout=self.timeout * 3,
            )
        if resp.status_code != 200:
            raise LLMError(f"Transcription returned {resp.status_code}: {resp.text[:500]}")
        return resp.json().get("text", "")


def parse_json_reply(text: str) -> dict:
    """Pull the first JSON object out of a model reply (tolerates ```json fences and preamble)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("Model reply contained no JSON object")
        candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise LLMError(f"Model reply was not valid JSON: {e}") from e


CITATION = re.compile(r"\[([A-Z]+\d+)\]")


def cited_ids(text: str) -> list[str]:
    return CITATION.findall(text or "")


def strip_unknown_citations(text: str, known: set[str]) -> tuple[str, list[str]]:
    """Remove citations that point at evidence we never supplied; return (clean_text, removed_ids)."""
    removed: list[str] = []

    def repl(m: re.Match) -> str:
        if m.group(1) in known:
            return m.group(0)
        removed.append(m.group(1))
        return ""

    return CITATION.sub(repl, text or ""), removed
