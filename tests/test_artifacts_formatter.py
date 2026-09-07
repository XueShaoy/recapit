from pathlib import Path

import pytest

from recapit.artifacts import (
    checkpoint_paths,
    ensure_targets_available,
    load_summary_json,
    load_transcript_json,
    output_paths,
    write_summary_inputs,
    write_transcript_json,
)
from recapit.errors import ArtifactError
from recapit.formatter import format_timestamp, merge_paragraphs, render_markdown, render_transcript
from recapit.models import (
    ActionItem,
    RecordingSummary,
    Segment,
    SourceInfo,
    SummaryDocument,
    Transcription,
)


def transcription() -> Transcription:
    return Transcription(
        source=SourceInfo(
            path="/tmp/会议.m4a", name="会议.m4a", size_bytes=100, duration_seconds=6.0
        ),
        language="zh",
        engine="faster-whisper",
        model="small",
        segments=[
            Segment(start=0.9, end=1.5, text="今天讨论目标。"),
            Segment(start=1.6, end=2.0, text="先确认范围"),
            Segment(start=5.1, end=6.0, text="最后安排任务。"),
        ],
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.0, "[00:00:00]"), (59.9, "[00:00:59]"), (3661.8, "[01:01:01]")],
)
def test_format_timestamp(seconds: float, expected: str) -> None:
    assert format_timestamp(seconds) == expected


def test_format_timestamp_rejects_negative() -> None:
    with pytest.raises(ValueError):
        format_timestamp(-0.1)


def test_merge_paragraphs_uses_pause_and_preserves_order() -> None:
    paragraphs = merge_paragraphs(transcription().segments, pause_seconds=2.0, max_chars=240)
    assert [item.text for item in paragraphs] == ["今天讨论目标。先确认范围。", "最后安排任务。"]
    assert paragraphs[0].start == 0.9


def test_merge_paragraphs_inserts_cjk_punctuation() -> None:
    paragraphs = merge_paragraphs(
        [
            Segment(start=0.0, end=1.0, text="因为我们现在就是债这一块私募"),
            Segment(start=1.0, end=2.0, text="因为之前咱们FI这块没有在做业务"),
            Segment(start=5.1, end=6.0, text="那就先这样吧"),
        ],
        pause_seconds=2.0,
        max_chars=240,
    )
    assert paragraphs[0].text == "因为我们现在就是债这一块私募，因为之前咱们FI这块没有在做业务。"
    assert paragraphs[1].text == "那就先这样吧。"


def test_merge_paragraphs_marks_questions_ending_with_ma() -> None:
    paragraphs = merge_paragraphs(
        [
            Segment(start=0.0, end=1.0, text="你听明白了吗"),
            Segment(start=1.0, end=2.0, text="我再讲一遍"),
        ],
        pause_seconds=2.0,
        max_chars=240,
    )
    assert paragraphs[0].text == "你听明白了吗？我再讲一遍。"


def test_merge_paragraphs_normalizes_ascii_commas() -> None:
    paragraphs = merge_paragraphs(
        [Segment(start=0.0, end=1.0, text="对,然后呢,因为他这个现象就算是不好")],
        pause_seconds=2.0,
        max_chars=240,
    )
    assert paragraphs[0].text == "对，然后呢，因为他这个现象就算是不好。"


def test_render_transcript_modes() -> None:
    value = transcription()
    paragraph = render_transcript(value, timestamps="paragraph", pause_seconds=2, max_chars=240)
    segment = render_transcript(value, timestamps="segment", pause_seconds=2, max_chars=240)
    none = render_transcript(value, timestamps="none", pause_seconds=2, max_chars=240)
    assert paragraph.count("[") == 2
    assert segment.count("[") == 3
    assert "[" not in none
    assert "[00:00:00]" in paragraph


def test_markdown_section_order_and_empty_actions() -> None:
    value = transcription().with_summary(
        RecordingSummary(summary="简要总结", key_points=["结论一"], action_items=[]),
        provider="agent",
        model=None,
    )
    markdown = render_markdown(value, timestamps="none", pause_seconds=2, max_chars=240)
    positions = [
        markdown.index(heading)
        for heading in ("## AI 摘要", "## 关键结论", "## 待办事项", "## 完整转写")
    ]
    assert positions == sorted(positions)
    assert "未识别到明确待办事项" in markdown
    assert "[00:" not in markdown


def test_markdown_renders_action_details() -> None:
    value = transcription().with_summary(
        RecordingSummary(
            summary="总结",
            action_items=[ActionItem(task="提交方案", owner="张三", due="周五")],
        ),
        provider="agent",
        model=None,
    )
    markdown = render_markdown(value, timestamps="paragraph", pause_seconds=2, max_chars=240)
    assert "提交方案（负责人：张三；截止时间：周五）" in markdown


def test_json_round_trip_and_existing_target_policy(tmp_path: Path) -> None:
    paths = output_paths(Path("会议.m4a"), tmp_path)
    assert paths.markdown.name == "会议.md"
    assert paths.word.name == "会议.docx"
    assert paths.recap_json.name == "recap.json"
    assert paths.run_manifest.name == "run.json"
    original = transcription()
    write_transcript_json(paths.transcript_json, original, replace_existing=False)
    assert load_transcript_json(paths.transcript_json) == original
    with pytest.raises(ArtifactError, match="已存在"):
        ensure_targets_available((paths.transcript_json,), overwrite=False)
    ensure_targets_available((paths.transcript_json,), overwrite=True)


def test_summary_inputs_and_hash(tmp_path: Path) -> None:
    paths = output_paths(Path("会议.m4a"), tmp_path)
    value = transcription()
    write_transcript_json(paths.transcript_json, value, replace_existing=False)
    write_summary_inputs(paths, value, replace_existing=False)
    assert paths.transcript_text.read_text(encoding="utf-8").strip() == value.plain_text()
    template = paths.summary_template.read_text(encoding="utf-8")
    assert value.transcript_sha256 in template
    summary_path = tmp_path / "summary.json"
    summary_path.write_text(
        SummaryDocument(
            transcript_sha256=value.transcript_sha256,
            summary="摘要",
            key_points=[],
            action_items=[],
        ).model_dump_json(),
        encoding="utf-8",
    )
    assert load_summary_json(summary_path).summary == "摘要"
    assert checkpoint_paths(paths.transcript_json).directory == paths.directory
    assert checkpoint_paths(paths.transcript_json).word == paths.directory / "会议.docx"
