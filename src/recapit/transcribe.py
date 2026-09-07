from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

from recapit.errors import TranscriptionError
from recapit.models import Segment, Transcription


class Transcriber(Protocol):
    def transcribe(self, path: Path, *, duration_seconds: float) -> Transcription: ...


class FasterWhisperTranscriber:
    def __init__(
        self,
        *,
        model: str,
        language: str | None,
        device: str = "auto",
        compute_type: str = "default",
        model_factory: Any | None = None,
    ) -> None:
        self.model_name = model
        self.language = language
        self.device = device
        self.compute_type = compute_type
        self._model_factory = model_factory

    def _create_model(self) -> Any:
        if self._model_factory is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore[import-untyped]
            except ImportError as exc:  # pragma: no cover - dependency failure
                raise TranscriptionError("faster-whisper 未安装，请先运行 uv sync") from exc
            self._model_factory = WhisperModel
        try:
            return self._model_factory(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
        except Exception as exc:
            raise TranscriptionError(f"无法加载 Whisper 模型 {self.model_name}: {exc}") from exc

    def transcribe(self, path: Path, *, duration_seconds: float) -> Transcription:
        whisper_model = self._create_model()
        try:
            raw_segments, info = whisper_model.transcribe(
                str(path), language=self.language, vad_filter=True
            )
            segments = self._normalize_segments(raw_segments)
        except TranscriptionError:
            raise
        except Exception as exc:
            raise TranscriptionError(f"Whisper 转写失败: {exc}") from exc
        if not segments:
            raise TranscriptionError("未检测到可转写语音")
        detected_language = str(getattr(info, "language", None) or self.language or "unknown")
        return Transcription.for_source(
            path,
            duration_seconds=duration_seconds,
            language=detected_language,
            engine="faster-whisper",
            model=self.model_name,
            segments=segments,
        )

    @staticmethod
    def _normalize_segments(raw_segments: Iterable[Any]) -> list[Segment]:
        segments: list[Segment] = []
        for item in raw_segments:
            text = str(getattr(item, "text", "")).strip()
            if not text:
                continue
            try:
                segments.append(Segment(start=float(item.start), end=float(item.end), text=text))
            except (TypeError, ValueError) as exc:
                raise TranscriptionError(f"Whisper 返回了无效分段: {exc}") from exc
        segments.sort(key=lambda item: item.start)
        return segments
