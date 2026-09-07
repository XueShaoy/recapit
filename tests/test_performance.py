import json
from pathlib import Path

from recapit.performance import MAX_SAMPLES_PER_KEY, PerformanceHistory, run_config_key


def test_performance_sample_serialization_excludes_content(tmp_path: Path) -> None:
    store = PerformanceHistory(tmp_path / "history.json", hardware="test-hw")
    store.record_success(
        model="turbo",
        device="cpu",
        compute_type="int8",
        audio_seconds=120.0,
        inference_seconds=60.0,
        run_signature="signature-a",
    )
    raw = store.path.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["version"] == 1
    blob = json.dumps(payload)
    for forbidden in ("path", "filename", "hash", "sha256", "transcript", ".m4a", "你好"):
        assert forbidden not in blob
    key = run_config_key("test-hw", "turbo", "cpu", "int8", "signature-a")
    sample = payload["samples"][key][0]
    assert sample["hardware"] == "test-hw"
    assert sample["model"] == "turbo"
    assert sample["device"] == "cpu"
    assert sample["compute_type"] == "int8"
    assert sample["run_signature"] == "signature-a"
    assert sample["audio_seconds"] == 120.0
    assert sample["inference_seconds"] == 60.0
    assert sample["rtf"] == 0.5


def test_history_limits_recent_samples_and_median_source(tmp_path: Path) -> None:
    store = PerformanceHistory(tmp_path / "history.json", hardware="hw")
    for index in range(MAX_SAMPLES_PER_KEY + 3):
        store.record_success(
            model="turbo",
            device="auto",
            compute_type="int8",
            audio_seconds=10.0,
            inference_seconds=1.0 + index,
        )
    rtfs = store.matching_rtfs(model="turbo", device="auto", compute_type="int8")
    assert len(rtfs) == MAX_SAMPLES_PER_KEY
    assert rtfs[0] == (1.0 + 3) / 10.0


def test_history_separates_run_signatures(tmp_path: Path) -> None:
    store = PerformanceHistory(tmp_path / "history.json", hardware="hw")
    store.record_success(
        model="turbo",
        device="cpu",
        compute_type="int8",
        audio_seconds=100,
        inference_seconds=20,
        run_signature="a",
    )
    assert store.matching_rtfs(
        model="turbo", device="cpu", compute_type="int8", run_signature="a"
    ) == [0.2]
    assert (
        store.matching_rtfs(model="turbo", device="cpu", compute_type="int8", run_signature="b")
        == []
    )


def test_history_degrades_when_file_is_unusable(tmp_path: Path, monkeypatch) -> None:
    missing = PerformanceHistory(tmp_path / "missing.json", hardware="hw")
    assert missing.matching_rtfs(model="turbo", device="cpu", compute_type="int8") == []

    broken = tmp_path / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")
    assert (
        PerformanceHistory(broken, hardware="hw").matching_rtfs(
            model="turbo", device="cpu", compute_type="int8"
        )
        == []
    )

    unreadable = tmp_path / "unreadable.json"
    unreadable.write_text("{}", encoding="utf-8")
    original_read = Path.read_text

    def boom(self: Path, *args: object, **kwargs: object) -> str:
        if self == unreadable:
            raise OSError("permission denied")
        return original_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    assert (
        PerformanceHistory(unreadable, hardware="hw").matching_rtfs(
            model="turbo", device="cpu", compute_type="int8"
        )
        == []
    )

    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    target = readonly_dir / "history.json"
    store = PerformanceHistory(target, hardware="hw")

    def cannot_mkdir(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(Path, "mkdir", cannot_mkdir)
    store.record_success(
        model="turbo",
        device="cpu",
        compute_type="int8",
        audio_seconds=10.0,
        inference_seconds=4.0,
    )
    assert not target.exists()
