from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from recapit.artifacts import (
    ArtifactPaths,
    atomic_write_text,
    checkpoint_paths,
    ensure_targets_available,
    load_summary_json,
    load_transcript_json,
    output_paths,
    write_summary_inputs,
    write_transcript_json,
)
from recapit.chunking import (
    ChunkSpec,
    absolute_segments,
    absolute_words,
    aggregate_chunk_progress,
    build_chunk_plan,
    extracted_chunk,
    merge_chunk_segments,
    merge_chunk_words,
)
from recapit.config import AppConfig
from recapit.errors import ArtifactError, TranscriptionError, WordExportError
from recapit.formatter import render_markdown
from recapit.identity import (
    resolve_device,
    run_signature,
    sanitize_stem,
    sha256_file,
    speaker_signature,
)
from recapit.media import validate_recording, validate_recording_path
from recapit.models import Segment, Transcription, WordTiming
from recapit.performance import PerformanceHistory
from recapit.progress import ProgressCallback, ProgressEvent, ProgressTracker, TranscriptionStage
from recapit.run_state import (
    ArtifactRecord,
    ChunkCheckpoint,
    ChunkRecord,
    ChunkStatus,
    RunLock,
    RunManifest,
    SpeakerStage,
    WorkflowStage,
    load_chunk_checkpoint,
    load_manifest,
    load_speaker_checkpoint,
    write_chunk_checkpoint,
    write_manifest,
    write_speaker_checkpoint,
)
from recapit.speakers import (
    PyannoteDiarizer,
    apply_speaker_labels,
    build_speaker_checkpoint,
    extracted_diarization_wav,
    require_speakers_extra,
)
from recapit.transcribe import FasterWhisperTranscriber, Transcriber
from recapit.word import markdown_to_docx

ChunkExtractor = Callable[[Path, ChunkSpec, Path], AbstractContextManager[Path]]


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    paths: ArtifactPaths
    transcription: Transcription


@dataclass(frozen=True, slots=True)
class RenderResult:
    markdown_path: Path
    word_path: Path | None
    json_path: Path
    transcription: Transcription


def transcribe_recording(
    recording: Path,
    config: AppConfig,
    *,
    transcriber: Transcriber | None = None,
    progress: ProgressCallback = lambda _event: None,
    performance_history: PerformanceHistory | None = None,
    restart: bool = False,
    chunk_extractor: ChunkExtractor | None = None,
    speaker_diarizer: PyannoteDiarizer | None = None,
    speaker_wav_extractor: Callable[[Path, Path], AbstractContextManager[Path]] | None = None,
) -> TranscriptionResult:
    config.validate()
    validate_recording_path(recording)
    tracker = ProgressTracker(0.0)
    progress(tracker.snapshot(TranscriptionStage.validating))
    duration = validate_recording(recording)
    source_digest = sha256_file(recording)
    paths = _resolve_output_paths(recording, config.output_dir, source_digest)
    plan = build_chunk_plan(
        duration,
        chunk_seconds=config.chunk_seconds,
        overlap_seconds=config.chunk_overlap_seconds,
    )
    actual_device = resolve_device(config.device)
    signature = run_signature(config, actual_device=actual_device)
    speakers_sig = speaker_signature(config) if config.speakers else None
    if config.speakers and speaker_diarizer is None:
        require_speakers_extra()
    manifest = _open_or_create_manifest(
        paths,
        recording,
        duration=duration,
        source_digest=source_digest,
        signature=signature,
        speakers_sig=speakers_sig,
        speakers_enabled=config.speakers,
        plan=plan,
        restart=restart,
    )
    tracker = ProgressTracker(duration)
    progress(tracker.snapshot(TranscriptionStage.validating))
    if _whisper_complete(manifest) and _speakers_satisfied(paths, manifest, config, speakers_sig):
        _verify_artifact(paths, manifest, "transcript", paths.transcript_json)
        stored_manifest = load_manifest(paths.run_manifest)
        if stored_manifest.source_path != manifest.source_path:
            with RunLock(paths.run_lock):
                write_manifest(paths.run_manifest, manifest)
        transcription = load_transcript_json(paths.transcript_json)
        progress(_terminal_event(TranscriptionStage.completed, tracker, transcription))
        return TranscriptionResult(paths, transcription)

    need_whisper = not _whisper_complete(manifest)
    active_transcriber = transcriber
    if need_whisper:
        active_transcriber = transcriber or FasterWhisperTranscriber(
            model=config.whisper_model,
            language=config.language,
            device=actual_device,
            compute_type=config.compute_type,
            beam_size=config.beam_size,
            vad_filter=config.vad_filter,
            hotwords=config.hotwords,
            word_timestamps=config.speakers,
        )
    extractor = chunk_extractor or extracted_chunk
    passthrough = transcriber is not None and chunk_extractor is None
    with RunLock(paths.run_lock):
        if restart or not paths.run_manifest.exists():
            write_manifest(paths.run_manifest, manifest)
        if manifest.stage is WorkflowStage.planned:
            manifest = manifest.with_stage(WorkflowStage.transcribing)
        if config.speakers:
            manifest = manifest.model_copy(
                update={
                    "speaker_stage": SpeakerStage.pending,
                    "speaker_signature": speakers_sig,
                }
            )
        write_manifest(paths.run_manifest, manifest)
        checkpoints: dict[int, ChunkCheckpoint] = {}
        completed_core = 0.0
        for record in manifest.chunks:
            if record.status is ChunkStatus.completed:
                checkpoint = _load_committed_chunk(paths, record, signature)
                checkpoints[record.spec.index] = checkpoint
                completed_core += record.spec.core_duration
        if completed_core:
            progress(
                ProgressEvent(
                    stage=TranscriptionStage.transcribing,
                    duration_seconds=duration,
                    processed_seconds=completed_core,
                    segment_count=sum(len(item.segments) for item in checkpoints.values()),
                    elapsed_seconds=tracker.elapsed_seconds(),
                    percent=min(completed_core / duration * 100.0, 99.0),
                )
            )
        if active_transcriber is not None:
            for index, record in enumerate(manifest.chunks):
                if record.status is ChunkStatus.completed:
                    continue
                context: AbstractContextManager[Path]
                context = (
                    nullcontext(recording)
                    if passthrough
                    else extractor(recording, record.spec, paths.directory)
                )
                with context as chunk_path:
                    local_segments, language, local_words = _transcribe_chunk(
                        active_transcriber,
                        chunk_path,
                        duration_seconds=record.spec.extract_duration,
                        progress=partial(
                            _emit_chunk_progress,
                            progress=progress,
                            spec=record.spec,
                            completed_core_seconds=completed_core,
                            total_seconds=duration,
                        ),
                    )
                checkpoint = ChunkCheckpoint(
                    spec=record.spec,
                    run_signature=signature,
                    language=language,
                    segments=absolute_segments(record.spec, local_segments),
                    words=absolute_words(record.spec, local_words) if config.speakers else [],
                )
                checkpoint_path = paths.chunks_directory / f"{record.spec.index:04d}.json"
                digest = write_chunk_checkpoint(checkpoint_path, checkpoint)
                updated_record = record.model_copy(
                    update={
                        "status": ChunkStatus.completed,
                        "path": str(checkpoint_path.relative_to(paths.directory)),
                        "sha256": digest,
                    }
                )
                records = list(manifest.chunks)
                records[index] = updated_record
                manifest = manifest.model_copy(update={"chunks": records})
                write_manifest(paths.run_manifest, manifest)
                checkpoints[record.spec.index] = checkpoint
                completed_core += record.spec.core_duration

        merged = merge_chunk_segments([(spec, checkpoints[spec.index].segments) for spec in plan])
        if not merged:
            raise TranscriptionError("未检测到可转写语音")
        language = next(
            (
                checkpoints[spec.index].language
                for spec in plan
                if checkpoints[spec.index].language != "unknown"
            ),
            config.language or "unknown",
        )
        words = merge_chunk_words([(spec, checkpoints[spec.index].words) for spec in plan])
        if config.speakers:
            merged = _apply_diarization(
                recording,
                config,
                paths,
                manifest,
                segments=merged,
                words=words,
                speakers_sig=speakers_sig,
                diarizer=speaker_diarizer,
                wav_extractor=speaker_wav_extractor,
            )
            manifest = load_manifest(paths.run_manifest)
        transcription = Transcription.for_source(
            recording,
            duration_seconds=duration,
            language=language,
            engine=config.engine,
            model=config.whisper_model,
            segments=merged,
        )
        progress(_terminal_event(TranscriptionStage.writing_checkpoint, tracker, transcription))
        write_transcript_json(paths.transcript_json, transcription, replace_existing=True)
        write_summary_inputs(paths, transcription, replace_existing=True)
        artifacts = dict(manifest.artifacts)
        for name, path in (
            ("transcript", paths.transcript_json),
            ("transcript_text", paths.transcript_text),
            ("summary_template", paths.summary_template),
        ):
            artifacts[name] = _artifact_record(paths, path)
        speaker_stage = SpeakerStage.complete if config.speakers else SpeakerStage.skipped
        manifest = _at_least_stage(
            manifest.model_copy(
                update={
                    "artifacts": artifacts,
                    "speaker_stage": speaker_stage,
                    "speaker_signature": speakers_sig,
                }
            ),
            WorkflowStage.transcribed,
        )
        write_manifest(paths.run_manifest, manifest)
    progress(_terminal_event(TranscriptionStage.completed, tracker, transcription))
    if active_transcriber is not None:
        _record_success(
            active_transcriber,
            config,
            audio_seconds=duration,
            history=performance_history,
            signature=signature,
            actual_device=actual_device,
        )
    return TranscriptionResult(paths, transcription)


def _resolve_output_paths(source: Path, output_root: Path, digest: str) -> ArtifactPaths:
    try:
        for manifest_path in output_root.glob("*/run.json"):
            try:
                if load_manifest(manifest_path).source_sha256 == digest:
                    transcription = manifest_path.parent / "transcript.json"
                    if transcription.exists():
                        return checkpoint_paths(transcription)
                    stem = manifest_path.parent.name.rsplit("-", 1)[0]
                    return _paths_in_existing(manifest_path.parent, stem)
            except ArtifactError:
                continue
    except OSError:
        pass
    paths = output_paths(source, output_root, source_sha256=digest)
    if paths.run_manifest.exists() and load_manifest(paths.run_manifest).source_sha256 != digest:
        stem = sanitize_stem(source)
        extended = f"{stem}-{digest[:16]}"
        return _paths_in_existing(output_root / extended, stem)
    return paths


def _paths_in_existing(directory: Path, stem: str) -> ArtifactPaths:
    return ArtifactPaths(
        directory=directory,
        markdown=directory / f"{stem}.md",
        word=directory / f"{stem}.docx",
        recap_json=directory / "recap.json",
        transcript_json=directory / "transcript.json",
        transcript_text=directory / "transcript.txt",
        summary_template=directory / "summary.template.json",
        summary_json=directory / "summary.json",
        run_manifest=directory / "run.json",
        chunks_directory=directory / "chunks",
        run_lock=directory / ".run.lock",
        speakers_json=directory / "speakers.json",
    )


def _open_or_create_manifest(
    paths: ArtifactPaths,
    recording: Path,
    *,
    duration: float,
    source_digest: str,
    signature: str,
    speakers_sig: str | None,
    speakers_enabled: bool,
    plan: list[ChunkSpec],
    restart: bool,
) -> RunManifest:
    if paths.run_manifest.exists() and not restart:
        manifest = load_manifest(paths.run_manifest)
        if manifest.source_sha256 != source_digest:
            raise ArtifactError("现有运行与源录音摘要不匹配；请使用其他输出目录")
        if manifest.run_signature != signature or [item.spec for item in manifest.chunks] != plan:
            raise ArtifactError("现有 chunk 与当前转写配置不兼容；请恢复原配置或使用 --restart")
        updates: dict[str, object] = {"source_path": str(recording.resolve())}
        if speakers_enabled:
            updates["speaker_signature"] = speakers_sig
            if manifest.speaker_signature != speakers_sig:
                updates["speaker_stage"] = SpeakerStage.pending
        return manifest.model_copy(update=updates)
    manifest = RunManifest(
        source_sha256=source_digest,
        source_size_bytes=recording.stat().st_size,
        source_duration_seconds=duration,
        source_path=str(recording.resolve()),
        run_signature=signature,
        speaker_signature=speakers_sig,
        speaker_stage=SpeakerStage.pending if speakers_enabled else SpeakerStage.skipped,
        chunks=[ChunkRecord(spec=spec) for spec in plan],
    )
    return manifest


def _load_committed_chunk(
    paths: ArtifactPaths, record: ChunkRecord, signature: str
) -> ChunkCheckpoint:
    assert record.path is not None and record.sha256 is not None
    checkpoint = load_chunk_checkpoint(paths.directory / record.path, expected_sha256=record.sha256)
    if checkpoint.run_signature != signature or checkpoint.spec != record.spec:
        raise ArtifactError(f"chunk {record.spec.index} 与运行 manifest 不匹配")
    return checkpoint


def _at_least_stage(manifest: RunManifest, stage: WorkflowStage) -> RunManifest:
    order = list(WorkflowStage)
    if order.index(manifest.stage) >= order.index(stage):
        return manifest
    return manifest.with_stage(stage)


def _whisper_complete(manifest: RunManifest) -> bool:
    return bool(manifest.chunks) and all(
        record.status is ChunkStatus.completed for record in manifest.chunks
    )


def _speakers_satisfied(
    paths: ArtifactPaths,
    manifest: RunManifest,
    config: AppConfig,
    speakers_sig: str | None,
) -> bool:
    if not _whisper_complete(manifest) or not paths.transcript_json.exists():
        return False
    if not config.speakers:
        return manifest.stage in {
            WorkflowStage.transcribed,
            WorkflowStage.summary_ready,
            WorkflowStage.rendered,
        }
    if manifest.speaker_stage is not SpeakerStage.complete:
        return False
    if manifest.speaker_signature != speakers_sig or "speakers" not in manifest.artifacts:
        return False
    _verify_artifact(paths, manifest, "speakers", paths.speakers_json)
    if "transcript" not in manifest.artifacts:
        return False
    try:
        _verify_artifact(paths, manifest, "transcript", paths.transcript_json)
    except ArtifactError:
        return False
    transcription = load_transcript_json(paths.transcript_json)
    return all(segment.speaker for segment in transcription.segments)


def _apply_diarization(
    recording: Path,
    config: AppConfig,
    paths: ArtifactPaths,
    manifest: RunManifest,
    *,
    segments: list[Segment],
    words: list[WordTiming],
    speakers_sig: str | None,
    diarizer: PyannoteDiarizer | None,
    wav_extractor: Callable[[Path, Path], AbstractContextManager[Path]] | None,
) -> list[Segment]:
    assert speakers_sig is not None
    if (
        paths.speakers_json.exists()
        and manifest.speaker_stage is SpeakerStage.complete
        and manifest.speaker_signature == speakers_sig
        and "speakers" in manifest.artifacts
    ):
        try:
            _verify_artifact(paths, manifest, "speakers", paths.speakers_json)
            checkpoint = load_speaker_checkpoint(paths.speakers_json)
            if checkpoint.speaker_signature == speakers_sig:
                labeled, _labels = apply_speaker_labels(
                    segments=segments,
                    words=words,
                    turns=list(checkpoint.turns),
                    label_map=dict(checkpoint.label_map),
                )
                return labeled
        except ArtifactError:
            pass
    engine = diarizer or PyannoteDiarizer()
    extractor = wav_extractor or extracted_diarization_wav
    with extractor(recording, paths.directory) as wav_path:
        turns = engine.diarize(wav_path, config=config)
    labeled, label_map = apply_speaker_labels(segments=segments, words=words, turns=turns)
    checkpoint = build_speaker_checkpoint(
        config, signature=speakers_sig, turns=turns, label_map=label_map
    )
    write_speaker_checkpoint(paths.speakers_json, checkpoint)
    artifacts = dict(manifest.artifacts)
    artifacts["speakers"] = _artifact_record(paths, paths.speakers_json)
    write_manifest(
        paths.run_manifest,
        manifest.model_copy(
            update={
                "artifacts": artifacts,
                "speaker_signature": speakers_sig,
                "speaker_stage": SpeakerStage.complete,
            }
        ),
    )
    return labeled


def _transcribe_chunk(
    transcriber: Transcriber,
    path: Path,
    *,
    duration_seconds: float,
    progress: ProgressCallback,
) -> tuple[list[Segment], str, list[WordTiming]]:
    chunk_method = getattr(transcriber, "transcribe_chunk", None)
    if callable(chunk_method):
        value = chunk_method(path, duration_seconds=duration_seconds, progress=progress)
        if len(value) == 3:
            segments, language, words = value
            return list(segments), str(language), list(words)
        segments, language = value
        return list(segments), str(language), []
    result = transcriber.transcribe(path, duration_seconds=duration_seconds, progress=progress)
    return result.segments, result.language, []


def _emit_chunk_progress(
    event: ProgressEvent,
    *,
    progress: ProgressCallback,
    spec: ChunkSpec,
    completed_core_seconds: float,
    total_seconds: float,
) -> None:
    progress(
        aggregate_chunk_progress(
            event,
            spec,
            completed_core_seconds=completed_core_seconds,
            total_seconds=total_seconds,
        )
    )


def _terminal_event(
    stage: TranscriptionStage,
    tracker: ProgressTracker,
    transcription: Transcription,
) -> ProgressEvent:
    return ProgressEvent(
        stage=stage,
        duration_seconds=tracker.duration_seconds,
        processed_seconds=tracker.duration_seconds,
        segment_count=len(transcription.segments),
        elapsed_seconds=tracker.elapsed_seconds(),
        percent=100.0,
        eta_seconds=0.0,
        latest_text=transcription.segments[-1].text,
        heartbeat=False,
    )


def _record_success(
    transcriber: Transcriber,
    config: AppConfig,
    *,
    audio_seconds: float,
    history: PerformanceHistory | None,
    signature: str,
    actual_device: str,
) -> None:
    inference = getattr(
        transcriber,
        "total_inference_seconds",
        getattr(transcriber, "last_inference_seconds", None),
    )
    if not isinstance(inference, (int, float)) or inference <= 0:
        return
    store = history if history is not None else PerformanceHistory()
    store.record_success(
        model=config.whisper_model,
        device=actual_device,
        compute_type=config.compute_type,
        audio_seconds=audio_seconds,
        inference_seconds=float(inference),
        run_signature=signature,
    )


def prepare_summary_inputs(
    transcript_json: Path, *, progress: Callable[[str], None] = lambda _message: None
) -> ArtifactPaths:
    progress("读取转写检查点")
    transcription = load_transcript_json(transcript_json)
    paths = checkpoint_paths(transcript_json)
    if paths.run_manifest.exists():
        manifest = load_manifest(paths.run_manifest)
        _verify_artifact(paths, manifest, "transcript", transcript_json)
    write_summary_inputs(paths, transcription, replace_existing=True)
    if paths.run_manifest.exists():
        artifacts = dict(manifest.artifacts)
        artifacts["transcript_text"] = _artifact_record(paths, paths.transcript_text)
        artifacts["summary_template"] = _artifact_record(paths, paths.summary_template)
        write_manifest(paths.run_manifest, manifest.model_copy(update={"artifacts": artifacts}))
    return paths


def render_recording(
    transcript_json: Path,
    summary_json: Path,
    config: AppConfig,
    *,
    word: bool = False,
    progress: Callable[[str], None] = lambda _message: None,
) -> RenderResult:
    config.validate()
    progress("校验转写与 Agent 总结")
    original_bytes = transcript_json.read_bytes()
    transcription = load_transcript_json(transcript_json)
    summary = load_summary_json(summary_json)
    if summary.transcript_sha256 != transcription.transcript_sha256:
        raise ArtifactError("Agent 总结与转写不匹配：transcript_sha256 不一致，未修改任何最终产物")
    paths = checkpoint_paths(transcript_json)
    if paths.run_manifest.exists():
        _verify_artifact(paths, load_manifest(paths.run_manifest), "transcript", transcript_json)
    targets = (
        (paths.recap_json, paths.markdown, paths.word)
        if word
        else (
            paths.recap_json,
            paths.markdown,
        )
    )
    ensure_targets_available(targets, overwrite=config.overwrite)
    completed = transcription.with_summary(summary.as_summary(), provider="agent", model=None)
    progress("渲染 Markdown 与最终 JSON")
    markdown = render_markdown(
        completed,
        timestamps=config.timestamps,
        pause_seconds=config.paragraph_pause_seconds,
        max_chars=config.paragraph_max_chars,
    )
    write_transcript_json(paths.recap_json, completed, replace_existing=config.overwrite)
    atomic_write_text(paths.markdown, markdown, replace_existing=config.overwrite)
    if transcript_json.read_bytes() != original_bytes:
        raise ArtifactError("原始 transcript.json 在渲染期间被意外修改")
    manifest = load_manifest(paths.run_manifest) if paths.run_manifest.exists() else None
    if manifest is not None:
        artifacts = dict(manifest.artifacts)
        artifacts["summary"] = _artifact_record(paths, summary_json)
        artifacts["recap"] = _artifact_record(paths, paths.recap_json)
        artifacts["markdown"] = _artifact_record(paths, paths.markdown)
        manifest = manifest.model_copy(update={"artifacts": artifacts})
        if manifest.stage is WorkflowStage.transcribed:
            manifest = manifest.with_stage(WorkflowStage.summary_ready)
        write_manifest(paths.run_manifest, manifest)
    word_path: Path | None = None
    if word:
        progress("生成 Word 文档")
        try:
            markdown_to_docx(markdown, paths.word, replace_existing=config.overwrite)
        except ArtifactError as exc:
            raise WordExportError(
                str(exc),
                markdown_path=str(paths.markdown.resolve()),
                json_path=str(paths.recap_json.resolve()),
            ) from exc
        word_path = paths.word.resolve()
        if manifest is not None:
            artifacts = dict(manifest.artifacts)
            artifacts["word"] = _artifact_record(paths, paths.word)
            manifest = manifest.model_copy(update={"artifacts": artifacts})
    if manifest is not None:
        if manifest.stage is not WorkflowStage.rendered:
            manifest = manifest.with_stage(WorkflowStage.rendered)
        write_manifest(paths.run_manifest, manifest)
    return RenderResult(paths.markdown.resolve(), word_path, paths.recap_json.resolve(), completed)


def _artifact_record(paths: ArtifactPaths, path: Path) -> ArtifactRecord:
    try:
        display_path = str(path.relative_to(paths.directory))
    except ValueError:
        display_path = str(path.resolve())
    return ArtifactRecord(path=display_path, sha256=sha256_file(path))


def _verify_artifact(
    paths: ArtifactPaths,
    manifest: RunManifest,
    name: str,
    expected_path: Path,
) -> None:
    record = manifest.artifacts.get(name)
    if record is None:
        raise ArtifactError(f"manifest 未提交产物: {name}")
    recorded_path = Path(record.path)
    path = recorded_path if recorded_path.is_absolute() else paths.directory / recorded_path
    try:
        matches = path.resolve() == expected_path.resolve() and sha256_file(path) == record.sha256
    except OSError as exc:
        raise ArtifactError(f"无法校验 manifest 产物 {expected_path}: {exc}") from exc
    if not matches:
        raise ArtifactError(f"manifest 产物摘要不匹配: {expected_path}")
