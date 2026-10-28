"""Client for Meta's Model API (Muse Spark for text, Muse Voice Transcribe for audio).

Configuration comes from the environment so no key ever lands in the repo:

    META_API_KEY            required
    META_API_BASE           API base URL (default: https://api.meta.ai/v1)
    META_MODEL              chat model id (default: muse-spark-1.3)
    META_TRANSCRIBE_MODEL   transcription model id (default: muse-voice-transcribe-1.0)

Text uses the OpenAI-compatible chat/completions endpoint. Audio uses Meta's own
POST /asr/transcribe (multipart: a JSON "request" part plus a WAV "audio" part), see
https://dev.meta.ai/docs/api-reference/voice/transcribe. Models and prices:
https://dev.meta.ai/products/meta-model-api
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import requests


DEFAULT_BASE = "https://api.meta.ai/v1"
DEFAULT_MODEL = "muse-spark-1.3"
DEFAULT_TRANSCRIBE_MODEL = "muse-voice-transcribe-1.0"
# The transcribe endpoint takes at most 10 minutes per request; split a little under that.
CHUNK_SECONDS = 570


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
        """Transcribe a call recording, one speaker turn per line: '[mm:ss] Speaker A: text'."""
        if not self.transcribe_model:
            raise LLMNotConfigured("Set META_TRANSCRIBE_MODEL to transcribe audio")
        lines: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            chunks = wav_chunks(audio_path, Path(tmp))
            for i, chunk in enumerate(chunks):
                data = self._transcribe_wav(chunk)
                offset_ms = i * CHUNK_SECONDS * 1000
                turns = data.get("turns") or []
                if not turns and data.get("transcript"):
                    turns = [{"startMs": 0, "transcript": data["transcript"]}]
                for t in turns:
                    text = " ".join(str(t.get("transcript", "")).split())
                    if not text:
                        continue
                    secs = (offset_ms + int(t.get("startMs") or 0)) // 1000
                    # Speaker labels restart in every chunk, so say which part they belong to.
                    part = f" (part {i + 1})" if len(chunks) > 1 else ""
                    who = f" Speaker {t['speaker']}{part}:" if t.get("speaker") else ""
                    lines.append(f"[{secs // 60:02d}:{secs % 60:02d}]{who} {text}")
        return "\n".join(lines)

    def _transcribe_wav(self, wav: Path) -> dict:
        def post(mode: str) -> requests.Response:
            request = {"model": self.transcribe_model, "audioEncoding": "WAV", "mode": mode}
            with open(wav, "rb") as f:
                return requests.post(
                    f"{self.base_url}/asr/transcribe",
                    headers={**self._headers(), "Accept": "application/json"},
                    files={
                        "request": (None, json.dumps(request), "application/json"),
                        "audio": (wav.name, f, "audio/wav"),
                    },
                    timeout=self.timeout * 3,
                )

        resp = post("DIARIZATION")
        if resp.status_code == 400 and "mode" in resp.text.lower():
            resp = post("ENDPOINTING")  # the model may not offer speaker attribution
        if resp.status_code != 200:
            raise LLMError(f"Transcription returned {resp.status_code}: {resp.text[:500]}")
        return resp.json()


def wav_chunks(audio_path: str | Path, workdir: Path) -> list[Path]:
    """Convert any recording to the 16 kHz mono PCM WAV the API needs, in pieces under the length limit."""
    audio_path = Path(audio_path)
    if not shutil.which("ffmpeg"):
        if audio_path.suffix.lower() == ".wav":
            return [audio_path]  # hope it is already 16 kHz mono PCM; the API says so if not
        raise LLMError("ffmpeg is needed to convert audio to WAV for transcription (or pass a 16 kHz mono WAV)")
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-i", str(audio_path), "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
         str(workdir / "chunk%03d.wav")],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise LLMError(f"Could not convert {audio_path.name} to WAV: {proc.stderr.strip()[:300]}")
    return sorted(workdir.glob("chunk*.wav"))


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
