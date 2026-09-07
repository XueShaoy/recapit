from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

from recapit.errors import TranscriptionError
from recapit.eta import RuntimeEta
from recapit.models import Segment, Transcription
from recapit.progress import ProgressCallback, ProgressEvent, ProgressTracker, TranscriptionStage

Clock = Callable[[], float]

ZH_INITIAL_PROMPT = "以下是带标点符号的中文转写。大家好，今天开会。"


def whisper_initial_prompt(language: str | None) -> str | None:
    if language and language.startswith("zh"):
        return ZH_INITIAL_PROMPT
    return None


class Transcriber(Protocol):
    def transcribe(
        self,
        path: Path,
        *,
        duration_seconds: float,
        progress: ProgressCallback | None = None,
    ) -> Transcription: ...


class FasterWhisperTranscriber:
    def __init__(
        self,
        *,
        model: str,
        language: str | None,
        device: str = "auto",
        compute_type: str = "default",
        beam_size: int = 5,
        vad_filter: bool = True,
        hotwords: tuple[str, ...] = (),
        model_factory: Any | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.model_name = model
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.vad_filter = vad_filter
        self.hotwords = hotwords
        self._model_factory = model_factory
        self._clock = clock
        self.last_load_seconds: float | None = None
        self.last_inference_seconds: float | None = None
        self.total_inference_seconds = 0.0
        self.actual_device = device
        self._model: Any | None = None

    def _now(self) -> float:
        if self._clock is not None:
            return self._clock()
        return time.monotonic()

    def _create_model(self) -> Any:
        if self._model is not None:
            return self._model
        if self._model_factory is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover - dependency failure
                raise TranscriptionError("faster-whisper 未安装，请先运行 uv sync") from exc
            self._model_factory = WhisperModel
        try:
            self._model = self._model_factory(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
            backend = getattr(self._model, "model", None)
            self.actual_device = str(getattr(backend, "device", self.device))
            return self._model
        except Exception as exc:
            raise TranscriptionError(f"无法加载 Whisper 模型 {self.model_name}: {exc}") from exc

    def transcribe(
        self,
        path: Path,
        *,
        duration_seconds: float,
        progress: ProgressCallback | None = None,
    ) -> Transcription:
        segments, detected_language = self.transcribe_chunk(
            path, duration_seconds=duration_seconds, progress=progress
        )
        if not segments:
            raise TranscriptionError("未检测到可转写语音")
        return Transcription.for_source(
            path,
            duration_seconds=duration_seconds,
            language=detected_language,
            engine="faster-whisper",
            model=self.model_name,
            segments=segments,
        )

    def transcribe_chunk(
        self,
        path: Path,
        *,
        duration_seconds: float,
        progress: ProgressCallback | None = None,
    ) -> tuple[list[Segment], str]:
        emit = progress or (lambda _event: None)
        tracker = ProgressTracker(duration_seconds, clock=self._clock or time.monotonic)
        emit(tracker.snapshot(TranscriptionStage.model_loading))
        load_started = self._now()
        whisper_model = self._create_model()
        self.last_load_seconds = max(self._now() - load_started, 0.0)
        emit(tracker.snapshot(TranscriptionStage.transcribing))
        eta = RuntimeEta()
        inference_started = self._now()
        try:
            raw_segments, info = whisper_model.transcribe(
                str(path),
                language=self.language,
                vad_filter=self.vad_filter,
                beam_size=self.beam_size,
                hotwords=",".join(self.hotwords) if self.hotwords else None,
                initial_prompt=whisper_initial_prompt(self.language),
            )
            segments = self._consume_segments(raw_segments, tracker, eta, emit, inference_started)
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"Whisper 转写失败: {exc}") from exc
        self.last_inference_seconds = max(self._now() - inference_started, 0.0)
        self.total_inference_seconds += self.last_inference_seconds
        emit(tracker.complete())
        detected_language = str(getattr(info, "language", None) or self.language or "unknown")
        return segments, detected_language

    def _consume_segments(
        self,
        raw_segments: Iterable[Any],
        tracker: ProgressTracker,
        eta: RuntimeEta,
        emit: ProgressCallback,
        inference_started: float,
    ) -> list[Segment]:
        segments: list[Segment] = []
        for item in raw_segments:
            segment = _normalize_segment(item)
            if segment is None:
                continue
            segments.append(segment)
            event = tracker.observe_segment(segment.end, segment.text)
            remaining = eta.update(
                inference_elapsed=max(self._now() - inference_started, 0.0),
                processed_seconds=event.processed_seconds,
                duration_seconds=tracker.duration_seconds,
            )
            emit(
                ProgressEvent(
                    stage=TranscriptionStage.transcribing,
                    duration_seconds=event.duration_seconds,
                    processed_seconds=event.processed_seconds,
                    segment_count=event.segment_count,
                    elapsed_seconds=event.elapsed_seconds,
                    percent=event.percent,
                    eta_seconds=remaining,
                    latest_text=event.latest_text,
                    heartbeat=False,
                )
            )
        segments.sort(key=lambda item: item.start)
        return segments


def _normalize_segment(item: Any) -> Segment | None:
    text = str(getattr(item, "text", "")).strip()
    if not text:
        return None
    try:
        return Segment(start=float(item.start), end=float(item.end), text=text)
    except (TypeError, ValueError) as exc:
        raise TranscriptionError(f"Whisper 返回了无效分段: {exc}") from exc


def huggingface_hub_dir() -> Path:
    explicit = os.environ.get("HUGGINGFACE_HUB_CACHE")
    if explicit:
        return Path(explicit)
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


_WHISPER_REPOS: dict[str, str] = {
    "tiny.en": "Systran/faster-whisper-tiny.en",
    "tiny": "Systran/faster-whisper-tiny",
    "base.en": "Systran/faster-whisper-base.en",
    "base": "Systran/faster-whisper-base",
    "small.en": "Systran/faster-whisper-small.en",
    "small": "Systran/faster-whisper-small",
    "medium.en": "Systran/faster-whisper-medium.en",
    "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large": "Systran/faster-whisper-large-v3",
    "distil-large-v2": "Systran/faster-distil-whisper-large-v2",
    "distil-medium.en": "Systran/faster-distil-whisper-medium.en",
    "distil-small.en": "Systran/faster-distil-whisper-small.en",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "distil-large-v3.5": "distil-whisper/distil-large-v3.5-ct2",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}


def whisper_model_cached(model: str) -> bool:
    repo = _WHISPER_REPOS.get(model, model if "/" in model else None)
    if repo is None:
        return False
    snapshots = huggingface_hub_dir() / f"models--{repo.replace('/', '--')}" / "snapshots"
    try:
        if not snapshots.is_dir():
            return False
        for snapshot in snapshots.iterdir():
            if (snapshot / "config.json").exists() or (snapshot / "model.bin").exists():
                return True
    except OSError:
        return False
    return False
