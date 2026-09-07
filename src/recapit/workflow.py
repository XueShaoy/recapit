from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
from recapit.config import AppConfig
from recapit.errors import ArtifactError
from recapit.formatter import render_markdown
from recapit.media import validate_recording, validate_recording_path
from recapit.models import Transcription
from recapit.transcribe import FasterWhisperTranscriber, Transcriber


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    paths: ArtifactPaths
    transcription: Transcription


@dataclass(frozen=True, slots=True)
class RenderResult:
    markdown_path: Path
    json_path: Path
    transcription: Transcription


def transcribe_recording(
    recording: Path,
    config: AppConfig,
    *,
    transcriber: Transcriber | None = None,
    progress: Callable[[str], None] = lambda _message: None,
) -> TranscriptionResult:
    config.validate()
    validate_recording_path(recording)
    paths = output_paths(recording, config.output_dir)
    ensure_targets_available(paths.transcription_targets, overwrite=config.overwrite)
    progress("校验录音")
    duration = validate_recording(recording)
    progress("Whisper 本地转写")
    active_transcriber = transcriber or FasterWhisperTranscriber(
        model=config.whisper_model,
        language=config.language,
        device=config.device,
        compute_type=config.compute_type,
    )
    transcription = active_transcriber.transcribe(recording, duration_seconds=duration)
    progress("保存转写检查点")
    write_transcript_json(paths.transcript_json, transcription, replace_existing=config.overwrite)
    write_summary_inputs(paths, transcription, replace_existing=config.overwrite)
    return TranscriptionResult(paths, transcription)


def prepare_summary_inputs(
    transcript_json: Path, *, progress: Callable[[str], None] = lambda _message: None
) -> ArtifactPaths:
    progress("读取转写检查点")
    transcription = load_transcript_json(transcript_json)
    paths = checkpoint_paths(transcript_json)
    write_transcript_json(paths.transcript_json, transcription, replace_existing=True)
    write_summary_inputs(paths, transcription, replace_existing=True)
    return paths


def render_recording(
    transcript_json: Path,
    summary_json: Path,
    config: AppConfig,
    *,
    progress: Callable[[str], None] = lambda _message: None,
) -> RenderResult:
    config.validate()
    progress("校验转写与 Agent 总结")
    transcription = load_transcript_json(transcript_json)
    summary = load_summary_json(summary_json)
    if summary.transcript_sha256 != transcription.transcript_sha256:
        raise ArtifactError(
            "Agent 总结与转写不匹配：transcript_sha256 不一致，未修改任何最终产物"
        )
    paths = checkpoint_paths(transcript_json)
    if paths.markdown.exists() and not config.overwrite:
        raise ArtifactError(f"目标产物已存在: {paths.markdown}；请使用 --overwrite")
    completed = transcription.with_summary(summary.as_summary(), provider="agent", model=None)
    progress("渲染 Markdown 与最终 JSON")
    markdown = render_markdown(
        completed,
        timestamps=config.timestamps,
        pause_seconds=config.paragraph_pause_seconds,
        max_chars=config.paragraph_max_chars,
    )
    write_transcript_json(paths.transcript_json, completed, replace_existing=True)
    atomic_write_text(paths.markdown, markdown, replace_existing=config.overwrite)
    return RenderResult(paths.markdown.resolve(), paths.transcript_json.resolve(), completed)

