import base64
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError

from recapit.artifacts import output_paths
from recapit.chunking import (
    ChunkSpec,
    absolute_segments,
    aggregate_chunk_progress,
    build_chunk_plan,
    extracted_chunk,
    merge_chunk_segments,
)
from recapit.config import AppConfig, load_config
from recapit.errors import ArtifactError, ConfigurationError, TranscriptionError
from recapit.identity import recording_id, run_signature, sanitize_stem, sha256_file
from recapit.models import Segment
from recapit.progress import ProgressEvent, TranscriptionStage
from recapit.run_state import (
    ChunkCheckpoint,
    ChunkRecord,
    ChunkStatus,
    RunLock,
    RunManifest,
    WorkflowStage,
    load_chunk_checkpoint,
    load_manifest,
    write_chunk_checkpoint,
    write_manifest,
)
from recapit.workflow import _resolve_output_paths, transcribe_recording


def test_source_identity_and_run_signature(tmp_path: Path) -> None:
    first = tmp_path / "同名?.m4a"
    second = tmp_path / "other" / "同名?.m4a"
    second.parent.mkdir()
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    first_hash = sha256_file(first)
    assert first_hash != sha256_file(second)
    assert sanitize_stem(first) == "同名"
    assert recording_id(first, first_hash).endswith(first_hash[:12])
    config = AppConfig()
    assert run_signature(config, actual_device="cpu") == run_signature(
        AppConfig(timestamps="none", speakers=True, max_speakers=5), actual_device="cpu"
    )
    assert run_signature(config, actual_device="cpu") != run_signature(
        AppConfig(beam_size=1), actual_device="cpu"
    )


def test_output_identity_extends_on_short_hash_collision(tmp_path: Path) -> None:
    source = tmp_path / "same.m4a"
    source.write_bytes(b"x")
    first_digest = "a" * 12 + "b" * 52
    second_digest = "a" * 12 + "c" * 52
    paths = output_paths(source, tmp_path, source_sha256=first_digest)
    write_manifest(
        paths.run_manifest,
        RunManifest(
            source_sha256=first_digest,
            source_size_bytes=1,
            source_duration_seconds=1,
            source_path=str(source),
            run_signature="d" * 64,
            chunks=[ChunkRecord(spec=build_chunk_plan(1)[0])],
        ),
    )
    resolved = _resolve_output_paths(source, tmp_path, second_digest)
    assert resolved.directory.name.endswith(second_digest[:16])


def test_nested_chunk_config_and_invalid_values(tmp_path: Path) -> None:
    path = tmp_path / "recapit.toml"
    path.write_text(
        """[transcription]
engine = "faster-whisper"
[transcription.chunking]
seconds = 600
overlap_seconds = 8
[transcription.decode]
beam_size = 3
vad_filter = false
hotwords = ["Recapit", "项目"]
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert (config.chunk_seconds, config.chunk_overlap_seconds) == (600, 8)
    assert config.hotwords == ("Recapit", "项目")
    assert config.beam_size == 3 and config.vad_filter is False
    with pytest.raises(ConfigurationError, match="engine"):
        AppConfig(engine="unknown").validate()
    with pytest.raises(ConfigurationError, match="chunk_overlap"):
        AppConfig(chunk_seconds=10, chunk_overlap_seconds=10).validate()


@pytest.mark.parametrize(
    ("duration", "cores"),
    [
        (899, [(0, 899)]),
        (900, [(0, 900)]),
        (901, [(0, 900), (900, 901)]),
        (1800, [(0, 900), (900, 1800)]),
        (3600, [(0, 900), (900, 1800), (1800, 2700), (2700, 3600)]),
    ],
)
def test_chunk_plan_boundaries(duration: float, cores: list[tuple[int, int]]) -> None:
    plan = build_chunk_plan(duration)
    assert [(item.core_start, item.core_end) for item in plan] == cores
    assert plan[0].extract_start == 0
    assert plan[-1].extract_end == duration
    if len(plan) > 2:
        assert plan[1].extract_start == 890 and plan[1].extract_end == 1810


def test_extracted_chunk_is_pcm_and_always_cleaned(tmp_path: Path) -> None:
    source = tmp_path / "tone.wav"
    encoded = (Path(__file__).parent / "fixtures" / "tone.wav.b64").read_text(encoding="ascii")
    source.write_bytes(base64.b64decode(encoded))
    spec = build_chunk_plan(0.2, chunk_seconds=0.2, overlap_seconds=0)[0]
    with extracted_chunk(source, spec, tmp_path) as chunk:
        assert chunk.exists() and chunk.read_bytes()[:4] == b"RIFF"
        generated = chunk
    assert not generated.exists()
    with pytest.raises(RuntimeError), extracted_chunk(source, spec, tmp_path) as interrupted:
        raise RuntimeError("stop")
    assert not interrupted.exists()


def test_absolute_merge_and_aggregate_progress() -> None:
    first, second = build_chunk_plan(1800)
    left = absolute_segments(first, [Segment(start=895, end=905, text="跨界句")])
    right = absolute_segments(second, [Segment(start=5, end=15, text="跨界句")])
    merged = merge_chunk_segments([(first, left), (second, right)])
    assert [item.text for item in merged] == ["跨界句"]
    assert merged[0].start == 895
    event = ProgressEvent(
        stage=TranscriptionStage.transcribing,
        duration_seconds=920,
        processed_seconds=460,
        segment_count=3,
        elapsed_seconds=10,
        percent=50,
    )
    aggregate = aggregate_chunk_progress(
        event, second, completed_core_seconds=900, total_seconds=1800
    )
    assert aggregate.processed_seconds == 1350
    assert aggregate.percent == 75


def test_merge_rejects_significant_overlap() -> None:
    spec = build_chunk_plan(900)[0]
    with pytest.raises(TranscriptionError, match="显著重叠"):
        merge_chunk_segments(
            [
                (
                    spec,
                    [
                        Segment(start=0, end=100, text="长段"),
                        Segment(start=50, end=60, text="冲突"),
                    ],
                )
            ]
        )


def test_manifest_checkpoint_round_trip_and_tamper_detection(tmp_path: Path) -> None:
    spec = build_chunk_plan(60)[0]
    signature = "a" * 64
    checkpoint = ChunkCheckpoint(
        spec=spec,
        run_signature=signature,
        language="zh",
        segments=[Segment(start=0, end=1, text="内容")],
    )
    chunk_path = tmp_path / "chunks" / "0000.json"
    digest = write_chunk_checkpoint(chunk_path, checkpoint)
    assert load_chunk_checkpoint(chunk_path, expected_sha256=digest) == checkpoint
    chunk_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ArtifactError, match="摘要不匹配"):
        load_chunk_checkpoint(chunk_path, expected_sha256=digest)

    manifest = RunManifest(
        source_sha256="b" * 64,
        source_size_bytes=1,
        source_duration_seconds=60,
        source_path="/tmp/a.m4a",
        run_signature=signature,
        chunks=[ChunkRecord(spec=spec)],
    )
    manifest_path = tmp_path / "run.json"
    write_manifest(manifest_path, manifest)
    assert load_manifest(manifest_path) == manifest
    assert manifest.with_stage(WorkflowStage.transcribing).stage is WorkflowStage.transcribing
    with pytest.raises(ArtifactError, match="倒退"):
        manifest.with_stage(WorkflowStage.rendered).with_stage(WorkflowStage.transcribed)
    raw = json.loads(manifest.model_dump_json())
    raw["schema_version"] = "2.0"
    with pytest.raises(ValidationError):
        RunManifest.model_validate(raw)
    with pytest.raises(ValidationError, match="requires path"):
        ChunkRecord(spec=spec, status=ChunkStatus.completed)


def test_atomic_checkpoint_and_manifest_preserve_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = build_chunk_plan(60)[0]
    checkpoint_path = tmp_path / "chunks" / "0000.json"
    checkpoint_path.parent.mkdir()
    checkpoint_path.write_text("previous", encoding="utf-8")
    checkpoint = ChunkCheckpoint(spec=spec, run_signature="a" * 64, language="zh", segments=[])

    def fail_replace(_source: str, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("recapit.artifacts.os.replace", fail_replace)
    with pytest.raises(ArtifactError, match="replace failed"):
        write_chunk_checkpoint(checkpoint_path, checkpoint)
    assert checkpoint_path.read_text(encoding="utf-8") == "previous"
    assert list(checkpoint_path.parent.glob(".0000.json.*.tmp")) == []


def test_run_lock_rejects_live_owner_and_reclaims_dead_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / ".run.lock"
    with RunLock(lock_path), pytest.raises(ArtifactError, match="占用"), RunLock(lock_path):
        pass
    lock_path.write_text('{"pid":99999999,"token":"dead"}', encoding="utf-8")
    with RunLock(lock_path):
        assert lock_path.exists()
    assert not lock_path.exists()


class IndexedSession:
    def __init__(
        self,
        current: dict[str, ChunkSpec],
        *,
        fail_index: int | None = None,
        empty_indexes: set[int] | None = None,
    ) -> None:
        self.current = current
        self.fail_index = fail_index
        self.empty_indexes = empty_indexes or set()
        self.calls: list[int] = []
        self.last_inference_seconds = 1.0

    def transcribe(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("chunk workflow should use transcribe_chunk")

    def transcribe_chunk(
        self, _path: Path, *, duration_seconds: float, progress: object
    ) -> tuple[list[Segment], str]:
        spec = self.current["spec"]
        self.calls.append(spec.index)
        if spec.index == self.fail_index:
            raise TranscriptionError("injected chunk failure")
        if spec.index in self.empty_indexes:
            return [], "zh"
        local_start = spec.core_start - spec.extract_start + 1
        if callable(progress):
            progress(
                ProgressEvent(
                    stage=TranscriptionStage.transcribing,
                    duration_seconds=duration_seconds,
                    processed_seconds=local_start + 1,
                    segment_count=1,
                    elapsed_seconds=1,
                    percent=1,
                )
            )
        return [Segment(start=local_start, end=local_start + 1, text=f"块{spec.index}")], "zh"


def indexed_extractor(current: dict[str, ChunkSpec]):
    @contextmanager
    def extract(source: Path, spec: ChunkSpec, _directory: Path) -> Iterator[Path]:
        current["spec"] = spec
        yield source

    return extract


def test_workflow_resumes_only_uncommitted_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "long.m4a"
    recording.write_bytes(b"local-audio")
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: 2700.0)
    current: dict[str, ChunkSpec] = {}
    first = IndexedSession(current, fail_index=2)
    with pytest.raises(TranscriptionError, match="injected"):
        transcribe_recording(
            recording,
            AppConfig(output_dir=tmp_path),
            transcriber=first,  # type: ignore[arg-type]
            chunk_extractor=indexed_extractor(current),
        )
    assert first.calls == [0, 1, 2]

    resumed = IndexedSession(current)
    resumed_events: list[ProgressEvent] = []
    result = transcribe_recording(
        recording,
        AppConfig(output_dir=tmp_path),
        transcriber=resumed,  # type: ignore[arg-type]
        chunk_extractor=indexed_extractor(current),
        progress=resumed_events.append,
    )
    assert resumed.calls == [2]
    assert [segment.text for segment in result.transcription.segments] == ["块0", "块1", "块2"]
    manifest = load_manifest(result.paths.run_manifest)
    assert manifest.stage is WorkflowStage.transcribed
    assert all(record.status is ChunkStatus.completed for record in manifest.chunks)
    assert any(event.percent >= 66 for event in resumed_events[:-1])


def test_resume_rejects_changed_signature_and_restart_reprocesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "long.m4a"
    recording.write_bytes(b"local-audio")
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: 1800.0)
    current: dict[str, ChunkSpec] = {}
    initial = IndexedSession(current, fail_index=1)
    with pytest.raises(TranscriptionError):
        transcribe_recording(
            recording,
            AppConfig(output_dir=tmp_path),
            transcriber=initial,  # type: ignore[arg-type]
            chunk_extractor=indexed_extractor(current),
        )
    with pytest.raises(ArtifactError, match="不兼容"):
        transcribe_recording(
            recording,
            AppConfig(output_dir=tmp_path, beam_size=1),
            transcriber=IndexedSession(current),  # type: ignore[arg-type]
            chunk_extractor=indexed_extractor(current),
        )
    restarted = IndexedSession(current)
    transcribe_recording(
        recording,
        AppConfig(output_dir=tmp_path),
        transcriber=restarted,  # type: ignore[arg-type]
        chunk_extractor=indexed_extractor(current),
        restart=True,
    )
    assert restarted.calls == [0, 1]


@pytest.mark.parametrize("duration", [1800.0, 2700.0, 3600.0])
def test_multi_chunk_merge_allows_silent_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, duration: float
) -> None:
    recording = tmp_path / f"audio-{int(duration)}.m4a"
    recording.write_bytes(str(duration).encode())
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: duration)
    current: dict[str, ChunkSpec] = {}
    count = len(build_chunk_plan(duration))
    session = IndexedSession(current, empty_indexes={1})
    result = transcribe_recording(
        recording,
        AppConfig(output_dir=tmp_path),
        transcriber=session,  # type: ignore[arg-type]
        chunk_extractor=indexed_extractor(current),
    )
    expected = [f"块{index}" for index in range(count) if index != 1]
    assert [item.text for item in result.transcription.segments] == expected


def test_all_silent_chunks_report_no_speech(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "silent.m4a"
    recording.write_bytes(b"silent")
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: 1800.0)
    current: dict[str, ChunkSpec] = {}
    with pytest.raises(TranscriptionError, match="未检测"):
        transcribe_recording(
            recording,
            AppConfig(output_dir=tmp_path),
            transcriber=IndexedSession(current, empty_indexes={0, 1}),  # type: ignore[arg-type]
            chunk_extractor=indexed_extractor(current),
        )


def test_same_content_moved_reuses_existing_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first.m4a"
    moved = tmp_path / "renamed.m4a"
    first.write_bytes(b"same-audio")
    moved.write_bytes(b"same-audio")
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: 2.0)
    current: dict[str, ChunkSpec] = {}
    original = transcribe_recording(
        first,
        AppConfig(output_dir=tmp_path / "out"),
        transcriber=IndexedSession(current),  # type: ignore[arg-type]
        chunk_extractor=indexed_extractor(current),
    )
    reused = IndexedSession(current)
    result = transcribe_recording(
        moved,
        AppConfig(output_dir=tmp_path / "out"),
        transcriber=reused,  # type: ignore[arg-type]
        chunk_extractor=indexed_extractor(current),
    )
    assert result.paths.directory == original.paths.directory
    assert reused.calls == []
    assert load_manifest(result.paths.run_manifest).source_path == str(moved.resolve())


def test_transcript_manifest_detects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "audio.m4a"
    recording.write_bytes(b"audio")
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: 2.0)
    current: dict[str, ChunkSpec] = {}
    result = transcribe_recording(
        recording,
        AppConfig(output_dir=tmp_path),
        transcriber=IndexedSession(current),  # type: ignore[arg-type]
        chunk_extractor=indexed_extractor(current),
    )
    result.paths.transcript_json.write_text("{}", encoding="utf-8")
    with pytest.raises(ArtifactError, match="摘要不匹配"):
        transcribe_recording(
            recording,
            AppConfig(output_dir=tmp_path),
            transcriber=IndexedSession(current),  # type: ignore[arg-type]
            chunk_extractor=indexed_extractor(current),
        )


def test_chunk_file_is_not_committed_when_manifest_update_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "audio.m4a"
    recording.write_bytes(b"audio")
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: 2.0)
    current: dict[str, ChunkSpec] = {}
    calls = {"count": 0}
    original = write_manifest

    def fail_third(path: Path, manifest: RunManifest) -> None:
        calls["count"] += 1
        if calls["count"] == 3:
            raise ArtifactError("manifest update failed")
        original(path, manifest)

    monkeypatch.setattr("recapit.workflow.write_manifest", fail_third)
    with pytest.raises(ArtifactError, match="manifest update failed"):
        transcribe_recording(
            recording,
            AppConfig(output_dir=tmp_path),
            transcriber=IndexedSession(current),  # type: ignore[arg-type]
            chunk_extractor=indexed_extractor(current),
        )
    manifests = list(tmp_path.glob("*/run.json"))
    assert len(manifests) == 1
    manifest = load_manifest(manifests[0])
    assert manifest.chunks[0].status is ChunkStatus.planned
    assert (manifests[0].parent / "chunks" / "0000.json").exists()


def test_unknown_engine_fails_before_media_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "a.m4a"
    recording.write_bytes(b"x")
    called = {"probe": False}

    def probe(_path: Path) -> float:
        called["probe"] = True
        return 1

    monkeypatch.setattr("recapit.workflow.validate_recording", probe)
    with pytest.raises(ConfigurationError, match="engine"):
        transcribe_recording(recording, AppConfig(engine="other"))
    assert called["probe"] is False
