from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

EstimateSource = Literal["history", "first_run"]

MIN_ETA_ELAPSED_SECONDS = 8.0
MIN_ETA_PROCESSED_SECONDS = 12.0
ETA_SMOOTHING = 0.35

# Conservative real-time factors: (low, high) inference seconds per audio second.
_CPU_RTF: dict[str, tuple[float, float]] = {
    "tiny": (0.12, 0.45),
    "base": (0.2, 0.7),
    "small": (0.35, 1.1),
    "medium": (0.7, 2.2),
    "large": (1.2, 3.8),
    "turbo": (0.45, 1.8),
    "distil": (0.35, 1.3),
}
_ACCELERATED_RTF: dict[str, tuple[float, float]] = {
    "tiny": (0.03, 0.12),
    "base": (0.04, 0.16),
    "small": (0.06, 0.22),
    "medium": (0.1, 0.35),
    "large": (0.18, 0.6),
    "turbo": (0.08, 0.35),
    "distil": (0.06, 0.28),
}


@dataclass(frozen=True, slots=True)
class DurationEstimate:
    low_seconds: float
    high_seconds: float
    typical_seconds: float
    source: EstimateSource
    rtf_typical: float


class RuntimeEta:
    def __init__(self, *, alpha: float = ETA_SMOOTHING) -> None:
        self._alpha = alpha
        self._smoothed_rtf: float | None = None

    def update(
        self,
        *,
        inference_elapsed: float,
        processed_seconds: float,
        duration_seconds: float,
    ) -> float | None:
        if processed_seconds <= 0 or inference_elapsed <= 0:
            return None
        if (
            inference_elapsed < MIN_ETA_ELAPSED_SECONDS
            or processed_seconds < MIN_ETA_PROCESSED_SECONDS
        ):
            return None
        instant = inference_elapsed / processed_seconds
        if self._smoothed_rtf is None:
            self._smoothed_rtf = instant
        else:
            self._smoothed_rtf = self._alpha * instant + (1.0 - self._alpha) * self._smoothed_rtf
        remaining_audio = max(duration_seconds - processed_seconds, 0.0)
        return remaining_audio * self._smoothed_rtf


def model_family(model: str) -> str:
    name = model.lower()
    for family in ("turbo", "distil", "large", "medium", "small", "base", "tiny"):
        if family in name:
            return family
    return "small"


def device_is_accelerated(device: str) -> bool:
    lowered = device.lower()
    return lowered in {"cuda", "gpu", "metal", "rocm", "hip"}


def conservative_rtf_range(model: str, device: str) -> tuple[float, float]:
    family = model_family(model)
    table = _ACCELERATED_RTF if device_is_accelerated(device) else _CPU_RTF
    return table.get(family, _CPU_RTF["small"])


def estimate_from_first_run(
    *, duration_seconds: float, model: str, device: str
) -> DurationEstimate:
    low_rtf, high_rtf = conservative_rtf_range(model, device)
    typical_rtf = (low_rtf + high_rtf) / 2.0
    return DurationEstimate(
        low_seconds=duration_seconds * low_rtf,
        high_seconds=duration_seconds * high_rtf,
        typical_seconds=duration_seconds * typical_rtf,
        source="first_run",
        rtf_typical=typical_rtf,
    )


def estimate_from_history(
    *,
    duration_seconds: float,
    rtfs: list[float],
) -> DurationEstimate | None:
    valid = [value for value in rtfs if value > 0]
    if not valid:
        return None
    ordered = sorted(valid)
    typical = _median(ordered)
    if len(ordered) >= 3:
        low_rtf = ordered[len(ordered) // 4]
        high_rtf = ordered[(3 * len(ordered)) // 4]
        if high_rtf <= low_rtf:
            low_rtf = typical * 0.85
            high_rtf = typical * 1.2
    else:
        low_rtf = typical * 0.8
        high_rtf = typical * 1.3
    return DurationEstimate(
        low_seconds=duration_seconds * low_rtf,
        high_seconds=duration_seconds * high_rtf,
        typical_seconds=duration_seconds * typical,
        source="history",
        rtf_typical=typical,
    )


def estimate_transcription(
    *,
    duration_seconds: float,
    model: str,
    device: str,
    history_rtfs: list[float] | None = None,
) -> DurationEstimate:
    historical = estimate_from_history(duration_seconds=duration_seconds, rtfs=history_rtfs or [])
    if historical is not None:
        return historical
    return estimate_from_first_run(duration_seconds=duration_seconds, model=model, device=device)


def _median(ordered: list[float]) -> float:
    count = len(ordered)
    mid = count // 2
    if count % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
