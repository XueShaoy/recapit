from pathlib import Path

import pytest
from typer.testing import CliRunner

from recapit.artifacts import load_transcript_json
from recapit.cli import app
from recapit.config import AppConfig
from recapit.errors import ArtifactError
from recapit.models import Segment, SourceInfo, SummaryDocument, Transcription
from recapit.workflow import prepare_summary_inputs, render_recording


def make_transcription() -> Transcription:
    return Transcription(
        source=SourceInfo(path="/tmp/会议.m4a", name="会议.m4a", size_bytes=1, duration_seconds=2),
        language="zh", engine="test", model="small",
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
        summary="完成讨论。", key_points=["确认范围"], action_items=[],
    )
    summary_path = paths.summary_json
    summary_path.write_text(summary.model_dump_json(), encoding="utf-8")
    result = render_recording(
        transcript_path, summary_path,
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
