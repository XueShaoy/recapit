from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

Clock = Callable[[], float]
ProgressCallback = Callable[["ProgressEvent"], None]


class TranscriptionStage(StrEnum):
    validating = "validating"
    model_loading = "model_loading"
    transcribing = "transcribing"
    writing_checkpoint = "writing_checkpoint"
    completed = "completed"


STAGE_LABELS: dict[TranscriptionStage, str] = {
    TranscriptionStage.validating: "校验录音",
    TranscriptionStage.model_loading: "加载 Whisper 模型",
    TranscriptionStage.transcribing: "转写中",
    TranscriptionStage.writing_checkpoint: "保存转写检查点",
    TranscriptionStage.completed: "完成",
}


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    stage: TranscriptionStage
    duration_seconds: float
    processed_seconds: float
    segment_count: int
    elapsed_seconds: float
    percent: float
    eta_seconds: float | None = None
    latest_text: str | None = None
    heartbeat: bool = False


class ProgressTracker:
    """Monotonic transcription progress independent of terminal rendering."""

    def __init__(
        self,
        duration_seconds: float,
        *,
        clock: Clock | None = None,
        started_at: float | None = None,
    ) -> None:
        self.duration_seconds = max(float(duration_seconds), 0.0)
        self._clock: Clock = clock or time.monotonic
        self._started_at = self._clock() if started_at is None else started_at
        self._processed = 0.0
        self._segment_count = 0
        self._latest_text: str | None = None
        self._completed = False

    def elapsed_seconds(self) -> float:
        return max(self._clock() - self._started_at, 0.0)

    def observe_segment(self, end: float, text: str | None = None) -> ProgressEvent:
        clamped = min(max(float(end), 0.0), self.duration_seconds)
        self._processed = max(self._processed, clamped)
        self._segment_count += 1
        cleaned = (text or "").strip()
        if cleaned:
            self._latest_text = cleaned
        return self.snapshot(TranscriptionStage.transcribing)

    def complete(
        self, stage: TranscriptionStage = TranscriptionStage.transcribing
    ) -> ProgressEvent:
        self._completed = True
        self._processed = self.duration_seconds
        return self.snapshot(stage, force_percent=100.0)

    def snapshot(
        self,
        stage: TranscriptionStage,
        *,
        eta_seconds: float | None = None,
        heartbeat: bool = False,
        force_percent: float | None = None,
        include_text: bool = True,
    ) -> ProgressEvent:
        if force_percent is not None:
            percent = force_percent
        elif self._completed:
            percent = 100.0
        else:
            percent = _running_percent(self._processed, self.duration_seconds)
        return ProgressEvent(
            stage=stage,
            duration_seconds=self.duration_seconds,
            processed_seconds=self._processed,
            segment_count=self._segment_count,
            elapsed_seconds=self.elapsed_seconds(),
            percent=percent,
            eta_seconds=eta_seconds,
            latest_text=self._latest_text if include_text else None,
            heartbeat=heartbeat,
        )


def _running_percent(processed: float, duration: float) -> float:
    if duration <= 0:
        return 0.0
    raw = 100.0 * processed / duration
    return min(max(raw, 0.0), 99.0)


class ActivityHeartbeat:
    """Keeps elapsed time moving when no new segments arrive; stop freezes the snapshot."""

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock or time.monotonic
        self._event: ProgressEvent | None = None
        self._received_at = 0.0
        self._stopped = False
        self._lock = threading.Lock()

    def update(self, event: ProgressEvent) -> ProgressEvent:
        with self._lock:
            if self._stopped:
                return event
            self._event = event
            self._received_at = self._clock()
            return event

    def current(self, *, heartbeat: bool = True) -> ProgressEvent | None:
        with self._lock:
            if self._event is None:
                return None
            extra = 0.0 if self._stopped else max(self._clock() - self._received_at, 0.0)
            return replace(
                self._event,
                elapsed_seconds=self._event.elapsed_seconds + extra,
                heartbeat=heartbeat and extra > 0,
            )

    def stop(self) -> ProgressEvent | None:
        with self._lock:
            if self._stopped:
                return self._event
            if self._event is not None:
                extra = max(self._clock() - self._received_at, 0.0)
                self._event = replace(
                    self._event,
                    elapsed_seconds=self._event.elapsed_seconds + extra,
                    heartbeat=False,
                )
            self._stopped = True
            return self._event

    @property
    def stopped(self) -> bool:
        return self._stopped


class HeartbeatRefresher:
    """Background ticker that emits live snapshots until stopped."""

    def __init__(
        self,
        heartbeat: ActivityHeartbeat,
        emit: ProgressCallback,
        *,
        interval_seconds: float = 1.0,
        stage: TranscriptionStage | None = None,
    ) -> None:
        self._heartbeat = heartbeat
        self._emit = emit
        self._interval = interval_seconds
        self._stage = stage
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="recapit-progress", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if self._heartbeat.stopped:
                return
            snapshot = self._heartbeat.current(heartbeat=True)
            if snapshot is None:
                continue
            if self._stage is not None:
                snapshot = replace(snapshot, stage=self._stage, heartbeat=True)
            self._emit(snapshot)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._thread = None
