from recapit.eta import (
    MIN_ETA_ELAPSED_SECONDS,
    MIN_ETA_PROCESSED_SECONDS,
    RuntimeEta,
    estimate_transcription,
)


def test_first_run_estimate_uses_conservative_range() -> None:
    estimate = estimate_transcription(
        duration_seconds=100.0, model="turbo", device="cpu", history_rtfs=[]
    )
    assert estimate.source == "first_run"
    assert estimate.low_seconds < estimate.high_seconds
    assert estimate.typical_seconds == (estimate.low_seconds + estimate.high_seconds) / 2


def test_history_estimate_uses_median_and_marks_source() -> None:
    estimate = estimate_transcription(
        duration_seconds=100.0,
        model="turbo",
        device="cpu",
        history_rtfs=[0.4, 0.5, 0.6],
    )
    assert estimate.source == "history"
    assert estimate.rtf_typical == 0.5
    assert estimate.low_seconds == 40.0
    assert estimate.high_seconds == 60.0


def test_runtime_eta_waits_for_enough_samples() -> None:
    eta = RuntimeEta(alpha=1.0)
    assert eta.update(inference_elapsed=1.0, processed_seconds=2.0, duration_seconds=100.0) is None
    assert (
        eta.update(
            inference_elapsed=MIN_ETA_ELAPSED_SECONDS,
            processed_seconds=0.0,
            duration_seconds=100.0,
        )
        is None
    )
    remaining = eta.update(
        inference_elapsed=MIN_ETA_ELAPSED_SECONDS,
        processed_seconds=MIN_ETA_PROCESSED_SECONDS,
        duration_seconds=100.0,
    )
    assert remaining is not None
    expected_rtf = MIN_ETA_ELAPSED_SECONDS / MIN_ETA_PROCESSED_SECONDS
    assert remaining == (100.0 - MIN_ETA_PROCESSED_SECONDS) * expected_rtf


def test_runtime_eta_smooths_speed_changes() -> None:
    eta = RuntimeEta(alpha=0.5)
    first = eta.update(inference_elapsed=10.0, processed_seconds=20.0, duration_seconds=100.0)
    second = eta.update(inference_elapsed=40.0, processed_seconds=20.0, duration_seconds=100.0)
    assert first is not None and second is not None
    assert first < second
    unsmoothed = (100.0 - 20.0) * (40.0 / 20.0)
    assert second < unsmoothed
