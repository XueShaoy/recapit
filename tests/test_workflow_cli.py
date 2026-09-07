from pathlib import Path

import pytest
from typer.testing import CliRunner

from recapit.artifacts import load_transcript_json, output_paths
from recapit.cli import app
from recapit.config import AppConfig
from recapit.errors import ArtifactError, WordExportError
from recapit.models import Segment, SourceInfo, SummaryDocument, Transcription
from recapit.performance import PerformanceHistory
from recapit.progress import ProgressEvent, TranscriptionStage
from recapit.run_state import WorkflowStage, load_manifest
from recapit.workflow import (
    RenderResult,
    prepare_summary_inputs,
    render_recording,
    transcribe_recording,
)


def make_transcription() -> Transcription:
    return Transcription(
        source=SourceInfo(path="/tmp/会议.m4a", name="会议.m4a", size_bytes=1, duration_seconds=2),
        language="zh",
        engine="test",
        model="small",
        segments=[
            Segment(start=0, end=1, text="讨论目标"),
            Segment(start=1, end=2, text="安排任务"),
        ],
    )


def test_prepare_summary_and_render_without_api_key(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(make_transcription().model_dump_json(), encoding="utf-8")
    paths = prepare_summary_inputs(transcript_path)
    assert paths.transcript_text.exists() and paths.summary_template.exists()
    summary = SummaryDocument(
        transcript_sha256=load_transcript_json(transcript_path).transcript_sha256,
        summary="完成讨论。",
        key_points=["确认范围"],
        action_items=[],
    )
    summary_path = paths.summary_json
    summary_path.write_text(summary.model_dump_json(), encoding="utf-8")
    result = render_recording(
        transcript_path,
        summary_path,
        AppConfig(output_dir=tmp_path, timestamps="none", overwrite=True),
    )
    assert result.markdown_path.exists()
    assert result.word_path is None
    assert "Agent 子任务" in result.markdown_path.read_text(encoding="utf-8")


def test_render_word_preflight_checks_both_targets_before_mutation(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.json"
    original = make_transcription()
    transcript_path.write_text(original.model_dump_json(), encoding="utf-8")
    paths = prepare_summary_inputs(transcript_path)
    paths.summary_json.write_text(
        SummaryDocument(
            transcript_sha256=original.transcript_sha256,
            summary="完成讨论。",
        ).model_dump_json(),
        encoding="utf-8",
    )
    paths.word.write_bytes(b"keep-docx")

    with pytest.raises(ArtifactError, match="会议.docx"):
        render_recording(
            transcript_path,
            paths.summary_json,
            AppConfig(output_dir=tmp_path),
            word=True,
        )

    assert load_transcript_json(transcript_path).recording_summary is None
    assert not paths.markdown.exists()
    assert paths.word.read_bytes() == b"keep-docx"


def test_render_without_word_ignores_existing_docx(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.json"
    original = make_transcription()
    transcript_path.write_text(original.model_dump_json(), encoding="utf-8")
    paths = prepare_summary_inputs(transcript_path)
    paths.summary_json.write_text(
        SummaryDocument(
            transcript_sha256=original.transcript_sha256,
            summary="完成讨论。",
        ).model_dump_json(),
        encoding="utf-8",
    )
    paths.word.write_bytes(b"old-docx")

    result = render_recording(
        transcript_path,
        paths.summary_json,
        AppConfig(output_dir=tmp_path),
    )

    assert result.word_path is None
    assert paths.markdown.exists()
    assert paths.word.read_bytes() == b"old-docx"


def test_transcribe_ignores_existing_docx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    recording = tmp_path / "会议.m4a"
    recording.write_bytes(b"x")
    paths = output_paths(recording, tmp_path)
    paths.directory.mkdir(parents=True)
    paths.word.write_bytes(b"old-docx")
    _stub_media(monkeypatch)

    result = transcribe_recording(
        recording,
        AppConfig(output_dir=tmp_path),
        transcriber=RecordingTranscriber(),
        performance_history=PerformanceHistory(tmp_path / "perf.json", hardware="test-hw"),
    )

    assert result.paths.transcript_json.exists()
    assert paths.word.read_bytes() == b"old-docx"


def test_same_name_recordings_use_distinct_content_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "a" / "会议.m4a"
    second = tmp_path / "b" / "会议.m4a"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    _stub_media(monkeypatch)
    one = transcribe_recording(
        first, AppConfig(output_dir=tmp_path / "out"), transcriber=RecordingTranscriber()
    )
    two = transcribe_recording(
        second, AppConfig(output_dir=tmp_path / "out"), transcriber=RecordingTranscriber()
    )
    assert one.paths.directory != two.paths.directory
    assert one.paths.transcript_json.exists() and two.paths.transcript_json.exists()


def test_render_word_uses_same_markdown_and_skips_converter_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript_path = tmp_path / "transcript.json"
    original = make_transcription()
    transcript_path.write_text(original.model_dump_json(), encoding="utf-8")
    paths = prepare_summary_inputs(transcript_path)
    paths.summary_json.write_text(
        SummaryDocument(
            transcript_sha256=original.transcript_sha256,
            summary="完成讨论。",
        ).model_dump_json(),
        encoding="utf-8",
    )
    calls: list[tuple[str, Path, bool]] = []

    def fake_word(markdown: str, path: Path, *, replace_existing: bool) -> None:
        calls.append((markdown, path, replace_existing))
        path.write_bytes(b"docx")

    monkeypatch.setattr("recapit.workflow.markdown_to_docx", fake_word)
    default_result = render_recording(
        transcript_path,
        paths.summary_json,
        AppConfig(output_dir=tmp_path, overwrite=True),
    )
    assert default_result.word_path is None
    assert calls == []

    word_result = render_recording(
        transcript_path,
        paths.summary_json,
        AppConfig(output_dir=tmp_path, overwrite=True),
        word=True,
    )
    assert word_result.word_path == paths.word.resolve()
    assert len(calls) == 1
    assert calls[0][0] == paths.markdown.read_text(encoding="utf-8")
    assert calls[0][1:] == (paths.word, True)


def test_word_failure_preserves_base_and_checkpoint_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transcript_path = tmp_path / "transcript.json"
    original = make_transcription()
    transcript_path.write_text(original.model_dump_json(), encoding="utf-8")
    paths = prepare_summary_inputs(transcript_path)
    paths.summary_json.write_text(
        SummaryDocument(
            transcript_sha256=original.transcript_sha256,
            summary="完成讨论。",
        ).model_dump_json(),
        encoding="utf-8",
    )
    transcript_text = paths.transcript_text.read_text(encoding="utf-8")
    summary_json = paths.summary_json.read_text(encoding="utf-8")
    monkeypatch.setattr(
        "recapit.workflow.markdown_to_docx",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ArtifactError("转换失败")),
    )

    with pytest.raises(WordExportError, match="转换失败") as raised:
        render_recording(
            transcript_path,
            paths.summary_json,
            AppConfig(output_dir=tmp_path, overwrite=True),
            word=True,
        )

    assert Path(raised.value.markdown_path).exists()
    assert Path(raised.value.json_path).exists()
    assert paths.transcript_text.read_text(encoding="utf-8") == transcript_text
    assert paths.summary_json.read_text(encoding="utf-8") == summary_json
    assert load_transcript_json(transcript_path).recording_summary is None
    assert load_transcript_json(paths.recap_json).recording_summary is not None


def test_manifest_render_keeps_transcript_immutable_and_commits_recap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "会议.m4a"
    recording.write_bytes(b"audio")
    _stub_media(monkeypatch)
    transcribed = transcribe_recording(
        recording, AppConfig(output_dir=tmp_path), transcriber=RecordingTranscriber()
    )
    before = transcribed.paths.transcript_json.read_bytes()
    transcribed.paths.summary_json.write_text(
        SummaryDocument(
            transcript_sha256=transcribed.transcription.transcript_sha256,
            summary="第一次总结",
        ).model_dump_json(),
        encoding="utf-8",
    )
    rendered = render_recording(
        transcribed.paths.transcript_json,
        transcribed.paths.summary_json,
        AppConfig(overwrite=True),
    )
    assert rendered.json_path == transcribed.paths.recap_json.resolve()
    assert transcribed.paths.transcript_json.read_bytes() == before
    assert load_transcript_json(rendered.json_path).recording_summary.summary == "第一次总结"
    assert load_manifest(transcribed.paths.run_manifest).stage is WorkflowStage.rendered

    transcribed.paths.summary_json.write_text(
        SummaryDocument(
            transcript_sha256=transcribed.transcription.transcript_sha256,
            summary="第二次总结",
        ).model_dump_json(),
        encoding="utf-8",
    )
    render_recording(
        transcribed.paths.transcript_json,
        transcribed.paths.summary_json,
        AppConfig(overwrite=True),
    )
    assert transcribed.paths.transcript_json.read_bytes() == before
    assert (
        load_transcript_json(transcribed.paths.recap_json).recording_summary.summary == "第二次总结"
    )


def test_manifest_records_partial_render_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "会议.m4a"
    recording.write_bytes(b"audio")
    _stub_media(monkeypatch)
    transcribed = transcribe_recording(
        recording, AppConfig(output_dir=tmp_path), transcriber=RecordingTranscriber()
    )
    transcribed.paths.summary_json.write_text(
        SummaryDocument(
            transcript_sha256=transcribed.transcription.transcript_sha256,
            summary="总结",
        ).model_dump_json(),
        encoding="utf-8",
    )

    def fail_markdown(*_args: object, **_kwargs: object) -> None:
        raise ArtifactError("markdown failed")

    monkeypatch.setattr("recapit.workflow.atomic_write_text", fail_markdown)
    with pytest.raises(ArtifactError, match="markdown failed"):
        render_recording(
            transcribed.paths.transcript_json,
            transcribed.paths.summary_json,
            AppConfig(overwrite=True),
        )
    manifest = load_manifest(transcribed.paths.run_manifest)
    assert manifest.stage is WorkflowStage.transcribed
    assert "recap" not in manifest.artifacts


def test_manifest_preserves_base_when_word_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "会议.m4a"
    recording.write_bytes(b"audio")
    _stub_media(monkeypatch)
    transcribed = transcribe_recording(
        recording, AppConfig(output_dir=tmp_path), transcriber=RecordingTranscriber()
    )
    transcribed.paths.summary_json.write_text(
        SummaryDocument(
            transcript_sha256=transcribed.transcription.transcript_sha256,
            summary="总结",
        ).model_dump_json(),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "recapit.workflow.markdown_to_docx",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ArtifactError("word failed")),
    )
    with pytest.raises(WordExportError, match="word failed"):
        render_recording(
            transcribed.paths.transcript_json,
            transcribed.paths.summary_json,
            AppConfig(overwrite=True),
            word=True,
        )
    manifest = load_manifest(transcribed.paths.run_manifest)
    assert manifest.stage is WorkflowStage.summary_ready
    assert {"recap", "markdown"} <= manifest.artifacts.keys()
    assert "word" not in manifest.artifacts


def test_cli_render_word_option_output_and_partial_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    render_help = CliRunner().invoke(app, ["render", "--help"])
    assert render_help.exit_code == 0
    assert "--word" in render_help.stdout
    assert "--no-word" in render_help.stdout

    markdown = tmp_path / "会议.md"
    word = tmp_path / "会议.docx"
    final_json = tmp_path / "transcript.json"
    captured: list[bool] = []

    def fake_render(
        _transcript: Path,
        _summary: Path,
        _config: AppConfig,
        *,
        word: bool = False,
        **_kwargs: object,
    ) -> RenderResult:
        captured.append(word)
        word_path = Path(tmp_path / "会议.docx") if word else None
        return RenderResult(markdown, word_path, final_json, make_transcription())

    monkeypatch.setattr("recapit.cli.render_recording", fake_render)
    runner = CliRunner()
    default = runner.invoke(
        app,
        ["render", "--transcript", "transcript.json", "--summary", "summary.json"],
    )
    enabled = runner.invoke(
        app,
        [
            "render",
            "--transcript",
            "transcript.json",
            "--summary",
            "summary.json",
            "--word",
        ],
    )
    assert default.exit_code == enabled.exit_code == 0
    assert captured == [False, True]
    assert "Word:" not in default.stdout
    assert f"Word: {word}" in enabled.stdout

    def fail_render(*_args: object, **_kwargs: object) -> RenderResult:
        raise WordExportError("转换失败", markdown_path=str(markdown), json_path=str(final_json))

    monkeypatch.setattr("recapit.cli.render_recording", fail_render)
    failed = runner.invoke(
        app,
        [
            "render",
            "--transcript",
            "transcript.json",
            "--summary",
            "summary.json",
            "--word",
        ],
    )
    assert failed.exit_code == 1
    combined = failed.stdout + failed.stderr
    assert "Word 导出失败" in combined
    assert f"Markdown: {markdown}" in combined
    assert f"JSON: {final_json}" in combined


def test_render_rejects_mismatched_summary_without_mutation(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.json"
    transcript_path.write_text(make_transcription().model_dump_json(), encoding="utf-8")
    paths = prepare_summary_inputs(transcript_path)
    paths.markdown.write_text("keep", encoding="utf-8")
    paths.summary_json.write_text(
        SummaryDocument(transcript_sha256="0" * 64, summary="错误").model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(ArtifactError, match="不匹配"):
        render_recording(transcript_path, paths.summary_json, AppConfig(output_dir=tmp_path))
    assert paths.markdown.read_text(encoding="utf-8") == "keep"


def test_cli_help_exposes_staged_commands_without_api_key() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "transcribe" in result.stdout
    assert "prepare-summary" in result.stdout
    assert "OPENAI_API_KEY" not in result.stdout
    transcribe_help = CliRunner().invoke(app, ["transcribe", "--help"])
    assert transcribe_help.exit_code == 0
    assert "--live-text" in transcribe_help.stdout
    assert "--no-live-text" in transcribe_help.stdout
    assert "--restart" in transcribe_help.stdout
    assert "--overwrite" not in transcribe_help.stdout


class RecordingTranscriber:
    def __init__(self, *, fail: str | None = None) -> None:
        self.fail = fail
        self.last_inference_seconds = 9.0

    def transcribe(
        self,
        path: Path,
        *,
        duration_seconds: float,
        progress: object = None,
    ) -> Transcription:
        emit = progress if callable(progress) else (lambda _event: None)
        emit(
            ProgressEvent(
                stage=TranscriptionStage.model_loading,
                duration_seconds=duration_seconds,
                processed_seconds=0.0,
                segment_count=0,
                elapsed_seconds=1.0,
                percent=0.0,
            )
        )
        if self.fail == "load":
            from recapit.errors import TranscriptionError

            raise TranscriptionError("无法加载 Whisper 模型 boom")
        emit(
            ProgressEvent(
                stage=TranscriptionStage.transcribing,
                duration_seconds=duration_seconds,
                processed_seconds=0.0,
                segment_count=0,
                elapsed_seconds=2.0,
                percent=0.0,
            )
        )
        if self.fail == "empty":
            from recapit.errors import TranscriptionError

            raise TranscriptionError("未检测到可转写语音")
        emit(
            ProgressEvent(
                stage=TranscriptionStage.transcribing,
                duration_seconds=duration_seconds,
                processed_seconds=duration_seconds,
                segment_count=2,
                elapsed_seconds=8.0,
                percent=99.0,
                latest_text="安排任务",
            )
        )
        emit(
            ProgressEvent(
                stage=TranscriptionStage.transcribing,
                duration_seconds=duration_seconds,
                processed_seconds=duration_seconds,
                segment_count=2,
                elapsed_seconds=8.0,
                percent=100.0,
            )
        )
        return make_transcription()


def _stub_media(monkeypatch: pytest.MonkeyPatch, duration: float = 2.0) -> None:
    monkeypatch.setattr("recapit.workflow.validate_recording_path", lambda _path: None)
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: duration)


def test_workflow_progress_order_success_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "会议.m4a"
    recording.write_bytes(b"x")
    _stub_media(monkeypatch)
    events: list[ProgressEvent] = []
    history = PerformanceHistory(tmp_path / "perf.json", hardware="test-hw")
    result = transcribe_recording(
        recording,
        AppConfig(output_dir=tmp_path, overwrite=True),
        transcriber=RecordingTranscriber(),
        progress=events.append,
        performance_history=history,
    )
    assert result.paths.transcript_json.exists()
    stages = [event.stage for event in events]
    assert stages[:4] == [
        TranscriptionStage.validating,
        TranscriptionStage.validating,
        TranscriptionStage.model_loading,
        TranscriptionStage.transcribing,
    ]
    assert stages[-2:] == [
        TranscriptionStage.writing_checkpoint,
        TranscriptionStage.completed,
    ]
    assert events[-1].percent == 100.0
    from recapit.identity import run_signature

    signature = run_signature(AppConfig(output_dir=tmp_path, overwrite=True), actual_device="cpu")
    rtfs = history.matching_rtfs(
        model="turbo", device="cpu", compute_type="int8", run_signature=signature
    )
    assert rtfs == [9.0 / 2.0]


def test_workflow_empty_speech_and_model_error_skip_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "会议.m4a"
    recording.write_bytes(b"x")
    _stub_media(monkeypatch)
    history = PerformanceHistory(tmp_path / "perf.json", hardware="test-hw")

    empty_events: list[ProgressEvent] = []
    with pytest.raises(Exception, match="未检测"):
        transcribe_recording(
            recording,
            AppConfig(output_dir=tmp_path, overwrite=True),
            transcriber=RecordingTranscriber(fail="empty"),
            progress=empty_events.append,
            performance_history=history,
        )
    assert TranscriptionStage.model_loading in [event.stage for event in empty_events]
    assert TranscriptionStage.transcribing in [event.stage for event in empty_events]
    assert TranscriptionStage.completed not in [event.stage for event in empty_events]
    assert history.matching_rtfs(model="turbo", device="auto", compute_type="int8") == []

    load_events: list[ProgressEvent] = []
    with pytest.raises(Exception, match="无法加载"):
        transcribe_recording(
            recording,
            AppConfig(output_dir=tmp_path / "other", overwrite=True),
            transcriber=RecordingTranscriber(fail="load"),
            progress=load_events.append,
            performance_history=history,
        )
    assert [event.stage for event in load_events][:3] == [
        TranscriptionStage.validating,
        TranscriptionStage.validating,
        TranscriptionStage.model_loading,
    ]
    assert TranscriptionStage.completed not in [event.stage for event in load_events]


def test_cli_transcribe_progress_privacy_and_live_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "会议.m4a"
    recording.write_bytes(b"x")
    config = tmp_path / "recapit.toml"
    config.write_text(
        """
[transcription]
model = "turbo"
device = "cpu"
compute_type = "int8"
[output]
directory = "out"
""",
        encoding="utf-8",
    )

    def fake_transcribe(recording_path: Path, app_config: AppConfig, **kwargs: object) -> object:
        progress = kwargs["progress"]
        assert callable(progress)
        progress(
            ProgressEvent(
                stage=TranscriptionStage.validating,
                duration_seconds=120.0,
                processed_seconds=0.0,
                segment_count=0,
                elapsed_seconds=0.0,
                percent=0.0,
            )
        )
        progress(
            ProgressEvent(
                stage=TranscriptionStage.transcribing,
                duration_seconds=120.0,
                processed_seconds=30.0,
                segment_count=3,
                elapsed_seconds=12.0,
                percent=25.0,
                latest_text="不该默认出现的正文",
            )
        )
        from recapit.artifacts import output_paths
        from recapit.workflow import TranscriptionResult

        paths = output_paths(recording_path, app_config.output_dir)
        paths.directory.mkdir(parents=True, exist_ok=True)
        paths.transcript_json.write_text("{}", encoding="utf-8")
        paths.transcript_text.write_text("x", encoding="utf-8")
        paths.summary_template.write_text("{}", encoding="utf-8")
        return TranscriptionResult(paths, make_transcription())

    monkeypatch.setattr("recapit.cli.transcribe_recording", fake_transcribe)
    monkeypatch.setattr("recapit.cli.whisper_model_cached", lambda _model: False)
    monkeypatch.setattr(
        "recapit.cli.PerformanceHistory",
        lambda: PerformanceHistory(tmp_path / "h.json", hardware="hw"),
    )
    runner = CliRunner()
    default = runner.invoke(
        app,
        [
            "transcribe",
            str(recording),
            "--config",
            str(config),
            "--output-dir",
            str(tmp_path / "a"),
        ],
    )
    assert default.exit_code == 0
    combined = default.stdout + default.stderr
    assert "录音时长" in combined
    assert "预计转写耗时" in combined
    assert "不该默认出现的正文" not in combined

    live = runner.invoke(
        app,
        [
            "transcribe",
            str(recording),
            "--config",
            str(config),
            "--output-dir",
            str(tmp_path / "b"),
            "--live-text",
        ],
    )
    assert live.exit_code == 0
    assert "不该默认出现的正文" in live.stdout + live.stderr


def test_cli_transcribe_error_and_interrupt_close_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "会议.m4a"
    recording.write_bytes(b"x")
    from recapit.errors import TranscriptionError

    def fail(**_kwargs: object) -> None:
        raise TranscriptionError("模型爆炸")

    monkeypatch.setattr(
        "recapit.cli.transcribe_recording",
        lambda *args, **kwargs: fail(),
    )
    result = CliRunner().invoke(app, ["transcribe", str(recording), "--output-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "错误：模型爆炸" in result.stdout + result.stderr

    def interrupted(*_args: object, **kwargs: object) -> None:
        progress = kwargs["progress"]
        assert callable(progress)
        progress(
            ProgressEvent(
                stage=TranscriptionStage.model_loading,
                duration_seconds=10.0,
                processed_seconds=0.0,
                segment_count=0,
                elapsed_seconds=1.0,
                percent=0.0,
            )
        )
        raise KeyboardInterrupt

    monkeypatch.setattr("recapit.cli.transcribe_recording", interrupted)
    interrupted_result = CliRunner().invoke(
        app, ["transcribe", str(recording), "--output-dir", str(tmp_path / "x")]
    )
    assert interrupted_result.exit_code == 130
    assert "已中断" in interrupted_result.stdout + interrupted_result.stderr
