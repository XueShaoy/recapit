from __future__ import annotations

import re
import sys
import threading
import time
from collections.abc import Callable
from typing import TextIO

from recapit.eta import DurationEstimate
from recapit.progress import (
    STAGE_LABELS,
    ActivityHeartbeat,
    HeartbeatRefresher,
    ProgressEvent,
    TranscriptionStage,
)

LIVE_TEXT_LIMIT = 48
TTY_REFRESH_SECONDS = 0.25
LOG_INTERVAL_SECONDS = 30.0
LOG_PERCENT_STEP = 5.0
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def format_clock(seconds: float) -> str:
    total = int(max(seconds, 0.0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_range(low: float, high: float) -> str:
    if high < low:
        low, high = high, low
    return f"{format_clock(low)}–{format_clock(high)}"


def sanitize_live_text(text: str | None, *, limit: int = LIVE_TEXT_LIMIT) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(limit - 1, 1)].rstrip() + "…"


def estimate_source_label(source: str) -> str:
    if source == "history":
        return "基于本机历史"
    return "首次估算，开始后动态校准"


class ProgressRenderer:
    def __init__(
        self,
        *,
        model: str,
        device: str,
        compute_type: str,
        estimate_for: Callable[[float], DurationEstimate],
        model_cached: bool,
        live_text: bool = False,
        stream: TextIO | None = None,
        is_tty: bool | None = None,
        clock: Callable[[], float] | None = None,
        heartbeat_interval: float | None = None,
        background: bool = True,
    ) -> None:
        self._model = model
        self._device = device
        self._compute_type = compute_type
        self._estimate_for = estimate_for
        self._model_cached = model_cached
        self._live_text = live_text
        self._stream = stream if stream is not None else sys.stderr
        self._is_tty = self._stream.isatty() if is_tty is None else is_tty
        self._clock = clock or time.monotonic
        self._heartbeat = ActivityHeartbeat(clock=self._clock)
        self._background = background
        interval = heartbeat_interval
        if interval is None:
            interval = TTY_REFRESH_SECONDS if self._is_tty else LOG_INTERVAL_SECONDS
        self._refresher = HeartbeatRefresher(
            self._heartbeat,
            self._on_heartbeat,
            interval_seconds=interval,
        )
        self._preamble_written = False
        self._closed = False
        self._lock = threading.Lock()
        self._last_log_time = -1e9
        self._last_log_percent = -100.0
        self._last_log_stage: TranscriptionStage | None = None
        self._tty_dirty = False
        self._spinner_index = 0
        self._last_tty_time = -1e9

    def handle(self, event: ProgressEvent) -> None:
        with self._lock:
            if self._closed:
                return
            self._heartbeat.update(event)
            self._write_preamble(event)
            if self._background and event.stage in {
                TranscriptionStage.model_loading,
                TranscriptionStage.transcribing,
            }:
                self._refresher.start()
            elif event.stage in {
                TranscriptionStage.writing_checkpoint,
                TranscriptionStage.completed,
            }:
                self._refresher.stop()
            self._render(event)

    def _on_heartbeat(self, event: ProgressEvent) -> None:
        with self._lock:
            if self._closed:
                return
            self._render(event)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._refresher.stop()
            self._heartbeat.stop()
            if self._is_tty and self._tty_dirty:
                self._stream.write("\n")
                self._stream.flush()
                self._tty_dirty = False

    def _write_preamble(self, event: ProgressEvent) -> None:
        if self._preamble_written or event.duration_seconds <= 0:
            return
        if self._is_tty and self._tty_dirty:
            self._stream.write("\n")
            self._tty_dirty = False
        estimate = self._estimate_for(event.duration_seconds)
        lines = [
            f"录音时长: {format_clock(event.duration_seconds)}",
            f"模型: {self._model}",
            f"设备: {self._device}",
            f"计算精度: {self._compute_type}",
            (
                f"预计转写耗时: {format_range(estimate.low_seconds, estimate.high_seconds)}"
                f"（{estimate_source_label(estimate.source)}）"
            ),
        ]
        if not self._model_cached:
            lines.append("首次模型下载时间不包含在上述转写耗时估算内")
        self._stream.write("\n".join(lines) + "\n")
        self._stream.flush()
        self._preamble_written = True

    def _render(self, event: ProgressEvent) -> None:
        if event.stage == TranscriptionStage.validating and event.duration_seconds <= 0:
            return
        if self._is_tty:
            self._render_tty(event)
            return
        if self._should_log(event):
            self._stream.write(self._status_line(event, spinner=False) + "\n")
            self._stream.flush()
            self._last_log_time = self._clock()
            self._last_log_percent = event.percent
            self._last_log_stage = event.stage

    def _should_log(self, event: ProgressEvent) -> bool:
        if event.stage != self._last_log_stage:
            return True
        if event.percent - self._last_log_percent >= LOG_PERCENT_STEP:
            return True
        return self._clock() - self._last_log_time >= LOG_INTERVAL_SECONDS

    def _render_tty(self, event: ProgressEvent) -> None:
        now = self._clock()
        changed = True
        if now - self._last_tty_time < TTY_REFRESH_SECONDS and event.heartbeat:
            changed = False
        if not changed:
            return
        self._spinner_index = (self._spinner_index + 1) % len(SPINNER)
        line = self._status_line(event, spinner=True)
        self._stream.write("\r" + line)
        self._stream.flush()
        self._tty_dirty = True
        self._last_tty_time = now

    def _status_line(self, event: ProgressEvent, *, spinner: bool) -> str:
        prefix = f"{SPINNER[self._spinner_index]} " if spinner else "→ "
        label = STAGE_LABELS[event.stage]
        percent = f"{int(event.percent)}%"
        position = f"{format_clock(event.processed_seconds)}/{format_clock(event.duration_seconds)}"
        eta = (
            f"剩余约 {format_clock(event.eta_seconds)}"
            if event.eta_seconds is not None
            else "预计剩余时间计算中"
        )
        parts = [
            f"{prefix}{label}",
            percent,
            position,
            f"分段 {event.segment_count}",
            f"已用 {format_clock(event.elapsed_seconds)}",
            eta,
        ]
        line = "  ".join(parts)
        if self._live_text:
            preview = sanitize_live_text(event.latest_text)
            if preview:
                line = f"{line}  {preview}"
        return line
