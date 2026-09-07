from io import StringIO

from recapit.eta import DurationEstimate
from recapit.progress import ProgressEvent, TranscriptionStage
from recapit.progress_display import ProgressRenderer, sanitize_live_text


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _estimate(_duration: float) -> DurationEstimate:
    return DurationEstimate(
        low_seconds=10.0,
        high_seconds=20.0,
        typical_seconds=15.0,
        source="first_run",
        rtf_typical=0.5,
    )


def _event(
    stage: TranscriptionStage,
    *,
    duration: float = 100.0,
    processed: float = 0.0,
    percent: float = 0.0,
    elapsed: float = 1.0,
    segments: int = 0,
    eta: float | None = None,
    text: str | None = None,
    heartbeat: bool = False,
) -> ProgressEvent:
    return ProgressEvent(
        stage=stage,
        duration_seconds=duration,
        processed_seconds=processed,
        segment_count=segments,
        elapsed_seconds=elapsed,
        percent=percent,
        eta_seconds=eta,
        latest_text=text,
        heartbeat=heartbeat,
    )


def _renderer(stream: StringIO, clock: FakeClock, **kwargs: object) -> ProgressRenderer:
    options = {
        "model": "turbo",
        "device": "auto",
        "compute_type": "int8",
        "estimate_for": _estimate,
        "model_cached": False,
        "live_text": False,
        "stream": stream,
        "is_tty": False,
        "clock": clock,
        "background": False,
    }
    options.update(kwargs)
    return ProgressRenderer(**options)  # type: ignore[arg-type]


def test_sanitize_live_text_strips_and_truncates() -> None:
    assert "\n" not in sanitize_live_text("第一行\n第二行")
    assert sanitize_live_text("x" * 80).endswith("…")


def test_non_tty_throttles_by_time_and_percent() -> None:
    stream = StringIO()
    clock = FakeClock()
    renderer = _renderer(stream, clock)
    renderer.handle(_event(TranscriptionStage.validating, duration=100.0))
    renderer.handle(_event(TranscriptionStage.model_loading, duration=100.0, elapsed=2))
    renderer.handle(
        _event(
            TranscriptionStage.transcribing,
            processed=10,
            percent=10,
            segments=1,
            elapsed=5,
            text="机密内容",
        )
    )
    renderer.handle(
        _event(
            TranscriptionStage.transcribing,
            processed=12,
            percent=12,
            segments=2,
            elapsed=6,
            heartbeat=True,
        )
    )
    clock.advance(30)
    renderer.handle(
        _event(
            TranscriptionStage.transcribing,
            processed=13,
            percent=13,
            segments=2,
            elapsed=36,
            heartbeat=True,
        )
    )
    renderer.handle(
        _event(
            TranscriptionStage.transcribing,
            processed=20,
            percent=18,
            segments=3,
            elapsed=37,
        )
    )
    renderer.close()
    output = stream.getvalue()
    assert "录音时长" in output
    assert "模型: turbo" in output
    assert "设备: auto" in output
    assert "计算精度: int8" in output
    assert "预计转写耗时" in output
    assert "首次估算，开始后动态校准" in output
    assert "首次模型下载时间不包含在上述转写耗时估算内" in output
    assert "机密内容" not in output
    status_lines = [line for line in output.splitlines() if line.startswith("→ ")]
    percents = [line for line in status_lines if "转写中" in line]
    assert len(percents) == 3


def test_tty_refreshes_single_line_and_finishes_with_newline() -> None:
    stream = StringIO()
    clock = FakeClock()
    renderer = _renderer(stream, clock, is_tty=True)
    clock.advance(0.3)
    renderer.handle(_event(TranscriptionStage.validating, duration=50.0))
    renderer.handle(
        _event(TranscriptionStage.transcribing, processed=10, percent=20, segments=2, elapsed=4)
    )
    clock.advance(0.3)
    renderer.handle(
        _event(
            TranscriptionStage.transcribing,
            processed=20,
            percent=40,
            segments=3,
            elapsed=8,
            eta=12.0,
        )
    )
    renderer.close()
    output = stream.getvalue()
    assert output.count("\r") >= 2
    assert output.endswith("\n")
    assert "20%" in output or "40%" in output
    assert "预计剩余时间计算中" in output or "剩余约" in output


def test_live_text_opt_in_includes_cleaned_preview() -> None:
    stream = StringIO()
    clock = FakeClock()
    renderer = _renderer(stream, clock, live_text=True)
    renderer.handle(_event(TranscriptionStage.validating, duration=30.0))
    renderer.handle(
        _event(
            TranscriptionStage.transcribing,
            processed=5,
            percent=10,
            segments=1,
            text="  机密\n片段  ",
        )
    )
    renderer.close()
    assert "机密 片段" in stream.getvalue()
