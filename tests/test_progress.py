from recapit.progress import ActivityHeartbeat, ProgressEvent, ProgressTracker, TranscriptionStage


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_progress_event_fields_and_stage_order() -> None:
    clock = FakeClock()
    tracker = ProgressTracker(100.0, clock=clock)
    validating = tracker.snapshot(TranscriptionStage.validating)
    assert validating.duration_seconds == 100.0
    assert validating.processed_seconds == 0.0
    assert validating.segment_count == 0
    assert validating.elapsed_seconds == 0.0
    assert validating.percent == 0.0
    assert validating.eta_seconds is None
    assert validating.latest_text is None

    clock.advance(2)
    loading = tracker.snapshot(TranscriptionStage.model_loading)
    assert loading.stage is TranscriptionStage.model_loading
    assert loading.elapsed_seconds == 2.0

    event = tracker.observe_segment(10.0, "你好")
    assert event.stage is TranscriptionStage.transcribing
    assert event.processed_seconds == 10.0
    assert event.segment_count == 1
    assert event.latest_text == "你好"
    assert event.percent == 10.0

    completed = tracker.complete(TranscriptionStage.completed)
    assert completed.percent == 100.0
    assert completed.processed_seconds == 100.0
    assert completed.stage is TranscriptionStage.completed


def test_percent_is_monotonic_and_capped_until_success() -> None:
    clock = FakeClock()
    tracker = ProgressTracker(50.0, clock=clock)
    percents: list[float] = []

    first = tracker.observe_segment(40.0, "后段")
    percents.append(first.percent)
    second = tracker.observe_segment(10.0, "乱序前段")
    percents.append(second.percent)
    assert second.processed_seconds == 40.0
    assert percents == sorted(percents)

    overflow = tracker.observe_segment(999.0, "超界")
    assert overflow.processed_seconds == 50.0
    assert overflow.percent == 99.0

    silence = tracker.snapshot(TranscriptionStage.transcribing, heartbeat=True)
    assert silence.processed_seconds == 50.0
    assert silence.percent == 99.0

    assert tracker.observe_segment(49.0, "仍未完成").percent == 99.0
    assert tracker.complete().percent == 100.0


def test_activity_heartbeat_advances_elapsed_and_stops() -> None:
    clock = FakeClock()
    tracker = ProgressTracker(80.0, clock=clock)
    heartbeat = ActivityHeartbeat(clock=clock)
    clock.advance(3)
    heartbeat.update(tracker.snapshot(TranscriptionStage.model_loading))
    clock.advance(5)
    live = heartbeat.current()
    assert live is not None
    assert live.elapsed_seconds == 8.0
    assert live.heartbeat is True
    assert live.processed_seconds == 0.0

    heartbeat.stop()
    clock.advance(20)
    frozen = heartbeat.current()
    assert frozen is not None
    assert frozen.elapsed_seconds == 8.0
    assert heartbeat.stopped is True
    later = ProgressEvent(
        stage=TranscriptionStage.transcribing,
        duration_seconds=80.0,
        processed_seconds=1.0,
        segment_count=1,
        elapsed_seconds=30.0,
        percent=1.0,
    )
    heartbeat.update(later)
    assert heartbeat.current() is not None
    assert heartbeat.current().processed_seconds == 0.0
