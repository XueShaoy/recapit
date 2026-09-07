from pathlib import Path

import pytest
from typer.testing import CliRunner

from recapit.artifacts import load_transcript_json
from recapit.cli import app
from recapit.config import AppConfig
from recapit.errors import ArtifactError
from recapit.models import Segment, SourceInfo, SummaryDocument, Transcription
from recapit.performance import PerformanceHistory
from recapit.progress import ProgressEvent, TranscriptionStage
from recapit.workflow import prepare_summary_inputs, render_recording, transcribe_recording


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
    assert "Agent 子任务" in result.markdown_path.read_text(encoding="utf-8")


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
    rtfs = history.matching_rtfs(model="turbo", device="auto", compute_type="int8")
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
