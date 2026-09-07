from __future__ import annotations

import json
import os
import platform
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HISTORY_VERSION = 1
MAX_SAMPLES_PER_KEY = 8


@dataclass(frozen=True, slots=True)
class PerformanceSample:
    hardware: str
    model: str
    device: str
    compute_type: str
    audio_seconds: float
    inference_seconds: float
    rtf: float
    recorded_at: str

    def to_json(self) -> dict[str, Any]:
        return {
            "hardware": self.hardware,
            "model": self.model,
            "device": self.device,
            "compute_type": self.compute_type,
            "audio_seconds": self.audio_seconds,
            "inference_seconds": self.inference_seconds,
            "rtf": self.rtf,
            "recorded_at": self.recorded_at,
        }


def hardware_fingerprint() -> str:
    uname = platform.uname()
    return f"{uname.system}-{uname.machine}-{uname.processor or uname.machine}"


def run_config_key(hardware: str, model: str, device: str, compute_type: str) -> str:
    return f"{hardware}|{model}|{device}|{compute_type}"


def default_history_path() -> Path:
    return cache_directory() / "performance-history.json"


def cache_directory() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return root / "recapit"
    if sys_platform() == "darwin":
        return Path.home() / "Library" / "Caches" / "recapit"
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".cache"
    return root / "recapit"


def sys_platform() -> str:
    return platform.system().lower()


class PerformanceHistory:
    def __init__(
        self,
        path: Path | None = None,
        *,
        hardware: str | None = None,
    ) -> None:
        self.path = path or default_history_path()
        self.hardware = hardware or hardware_fingerprint()

    def matching_rtfs(self, *, model: str, device: str, compute_type: str) -> list[float]:
        key = run_config_key(self.hardware, model, device, compute_type)
        return [sample.rtf for sample in self._samples_by_key().get(key, []) if sample.rtf > 0]

    def record_success(
        self,
        *,
        model: str,
        device: str,
        compute_type: str,
        audio_seconds: float,
        inference_seconds: float,
    ) -> None:
        if audio_seconds <= 0 or inference_seconds <= 0:
            return
        sample = PerformanceSample(
            hardware=self.hardware,
            model=model,
            device=device,
            compute_type=compute_type,
            audio_seconds=audio_seconds,
            inference_seconds=inference_seconds,
            rtf=inference_seconds / audio_seconds,
            recorded_at=datetime.now(UTC).isoformat(),
        )
        payload = self._load_payload()
        grouped = self._samples_from_payload(payload)
        key = run_config_key(sample.hardware, sample.model, sample.device, sample.compute_type)
        existing = grouped.get(key, [])
        existing.append(sample)
        grouped[key] = existing[-MAX_SAMPLES_PER_KEY:]
        self._save(grouped)

    def _samples_by_key(self) -> dict[str, list[PerformanceSample]]:
        return self._samples_from_payload(self._load_payload())

    def _load_payload(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": HISTORY_VERSION, "samples": {}}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return {"version": HISTORY_VERSION, "samples": {}}
        if not isinstance(raw, dict) or raw.get("version") != HISTORY_VERSION:
            return {"version": HISTORY_VERSION, "samples": {}}
        samples = raw.get("samples")
        if not isinstance(samples, dict):
            return {"version": HISTORY_VERSION, "samples": {}}
        return raw

    def _samples_from_payload(self, payload: dict[str, Any]) -> dict[str, list[PerformanceSample]]:
        grouped: dict[str, list[PerformanceSample]] = {}
        samples = payload.get("samples", {})
        if not isinstance(samples, dict):
            return grouped
        for key, items in samples.items():
            if not isinstance(key, str) or not isinstance(items, list):
                continue
            parsed: list[PerformanceSample] = []
            for item in items:
                sample = _parse_sample(item)
                if sample is not None:
                    parsed.append(sample)
            if parsed:
                grouped[key] = parsed
        return grouped

    def _save(self, grouped: dict[str, list[PerformanceSample]]) -> None:
        payload = {
            "version": HISTORY_VERSION,
            "samples": {
                key: [sample.to_json() for sample in items] for key, items in grouped.items()
            },
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        _atomic_write_best_effort(self.path, serialized)


def _parse_sample(item: object) -> PerformanceSample | None:
    if not isinstance(item, dict):
        return None
    try:
        hardware = str(item["hardware"])
        model = str(item["model"])
        device = str(item["device"])
        compute_type = str(item["compute_type"])
        audio_seconds = float(item["audio_seconds"])
        inference_seconds = float(item["inference_seconds"])
        rtf = float(item["rtf"])
        recorded_at = str(item["recorded_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if audio_seconds <= 0 or inference_seconds <= 0 or rtf <= 0:
        return None
    return PerformanceSample(
        hardware=hardware,
        model=model,
        device=device,
        compute_type=compute_type,
        audio_seconds=audio_seconds,
        inference_seconds=inference_seconds,
        rtf=rtf,
        recorded_at=recorded_at,
    )


def _atomic_write_best_effort(path: Path, content: str) -> None:
    temporary: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
