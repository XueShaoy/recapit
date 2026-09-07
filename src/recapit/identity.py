from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from recapit.config import AppConfig
from recapit.transcribe import whisper_initial_prompt

_UNSAFE_STEM = re.compile(r"[^\w\-. ]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def sanitize_stem(path: Path) -> str:
    value = unicodedata.normalize("NFKC", path.stem).strip()
    value = _UNSAFE_STEM.sub("-", value)
    value = _SPACES.sub(" ", value).strip(" .-")
    return value[:80].rstrip(" .-") or "recording"


def recording_id(path: Path, source_sha256: str) -> str:
    return f"{sanitize_stem(path)}-{source_sha256[:12]}"


def resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import ctranslate2  # type: ignore[import-untyped]

        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except (ImportError, RuntimeError):
        return "cpu"


def normalized_run_options(
    config: AppConfig, *, actual_device: str | None = None
) -> dict[str, Any]:
    return {
        "engine": config.engine,
        "model": config.whisper_model,
        "language": config.language,
        "device": actual_device or config.device,
        "compute_type": config.compute_type,
        "chunk_seconds": config.chunk_seconds,
        "chunk_overlap_seconds": config.chunk_overlap_seconds,
        "beam_size": config.beam_size,
        "vad_filter": config.vad_filter,
        "hotwords": list(config.hotwords),
        "initial_prompt": whisper_initial_prompt(config.language),
    }


def run_signature(config: AppConfig, *, actual_device: str | None = None) -> str:
    payload = json.dumps(
        normalized_run_options(config, actual_device=actual_device),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


SPEAKER_PIPELINE_ID = "pyannote/speaker-diarization-community-1"
SPEAKER_ALIGNMENT_VERSION = "exclusive-word-v1"


def speaker_signature(config: AppConfig) -> str:
    payload = json.dumps(
        {
            "pipeline": SPEAKER_PIPELINE_ID,
            "max_speakers": config.max_speakers,
            "num_speakers": config.num_speakers,
            "alignment": SPEAKER_ALIGNMENT_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
