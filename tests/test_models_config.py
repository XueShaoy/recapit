from pathlib import Path

import pytest
from pydantic import ValidationError

from recapit.config import AppConfig, load_config
from recapit.models import Segment, SourceInfo, SummaryDocument, Transcription


def make_transcription() -> Transcription:
    return Transcription(
        source=SourceInfo(
            path="/tmp/demo.m4a", name="demo.m4a", size_bytes=12, duration_seconds=3.0
        ),
        language="zh",
        engine="faster-whisper",
        model="small",
        segments=[Segment(start=0.0, end=1.5, text=" 你好 ")],
    )


def test_models_round_trip_and_trim_text() -> None:
    value = make_transcription()
    restored = Transcription.model_validate_json(value.model_dump_json())
    assert restored == value
    assert restored.segments[0].text == "你好"


def test_segment_rejects_invalid_interval() -> None:
    with pytest.raises(ValidationError, match="segment end"):
        Segment(start=2.0, end=1.0, text="错误")


def test_transcription_rejects_unsorted_segments() -> None:
    with pytest.raises(ValidationError, match="ordered"):
        Transcription(
            source=make_transcription().source,
            language="zh",
            engine="test",
            model="test",
            segments=[
                Segment(start=2, end=3, text="后"),
                Segment(start=0, end=1, text="前"),
            ],
        )


def test_config_precedence_and_auto_language(tmp_path: Path) -> None:
    config_file = tmp_path / "recapit.toml"
    config_file.write_text(
        """[transcription]
model = "base"
language = "zh"
[output]
timestamps = "paragraph"
""",
        encoding="utf-8",
    )
    config = load_config(
        config_file,
        overrides={"timestamps": "none", "language": "auto", "whisper_model": None},
    )
    assert config.whisper_model == "base"
    assert config.timestamps == "none"
    assert config.language is None


def test_config_contains_no_api_key_field() -> None:
    assert "api_key" not in AppConfig.__dataclass_fields__


def test_transcript_hash_and_summary_document_contract() -> None:
    value = make_transcription()
    assert value.transcript_sha256 == value.calculate_sha256()
    document = SummaryDocument(
        transcript_sha256=value.transcript_sha256,
        summary="摘要",
        key_points=["结论"],
        action_items=[],
    )
    assert document.as_summary().summary == "摘要"


def test_summary_document_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SummaryDocument(
            transcript_sha256="0" * 64,
            summary="摘要",
            key_points=[],
            action_items=[],
            unexpected="拒绝",
        )
