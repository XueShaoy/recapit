from __future__ import annotations

import math
import re
from dataclasses import dataclass

from recapit.models import RecordingSummary, Segment, TimestampMode, Transcription

_CJK = re.compile(r"[\u3400-\u9fff]")
_SENTENCE_ENDINGS = ("。", "！", "？", ".", "!", "?")


@dataclass(frozen=True, slots=True)
class Paragraph:
    start: float
    end: float
    text: str
    segments: tuple[Segment, ...]


def format_timestamp(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("时间秒数必须是非负有限值")
    total = math.floor(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"


def _join_text(left: str, right: str) -> str:
    if not left:
        return right
    if _CJK.search(left[-1]) or _CJK.search(right[0]):
        return left + right
    return f"{left} {right}"


def merge_paragraphs(
    segments: list[Segment], *, pause_seconds: float = 2.0, max_chars: int = 240
) -> list[Paragraph]:
    if not segments:
        return []
    paragraphs: list[Paragraph] = []
    current: list[Segment] = []
    text = ""
    for segment in segments:
        gap = segment.start - current[-1].end if current else 0.0
        combined = _join_text(text, segment.text)
        punctuation_break = bool(
            current and text.endswith(_SENTENCE_ENDINGS) and len(text) >= max_chars // 2
        )
        if current and (gap > pause_seconds or len(combined) > max_chars or punctuation_break):
            paragraphs.append(Paragraph(current[0].start, current[-1].end, text, tuple(current)))
            current = []
            text = ""
        current.append(segment)
        text = _join_text(text, segment.text)
    if current:
        paragraphs.append(Paragraph(current[0].start, current[-1].end, text, tuple(current)))
    return paragraphs


def render_transcript(
    transcription: Transcription,
    *,
    timestamps: TimestampMode,
    pause_seconds: float,
    max_chars: int,
) -> str:
    if timestamps == "segment":
        return "\n\n".join(
            f"{format_timestamp(segment.start)} {segment.text}"
            for segment in transcription.segments
        )
    paragraphs = merge_paragraphs(
        transcription.segments, pause_seconds=pause_seconds, max_chars=max_chars
    )
    if timestamps == "paragraph":
        return "\n\n".join(
            f"{format_timestamp(paragraph.start)} {paragraph.text}" for paragraph in paragraphs
        )
    if timestamps == "none":
        return "\n\n".join(paragraph.text for paragraph in paragraphs)
    raise ValueError(f"不支持的时间码模式: {timestamps}")


def render_markdown(
    transcription: Transcription,
    *,
    timestamps: TimestampMode,
    pause_seconds: float,
    max_chars: int,
) -> str:
    summary = transcription.recording_summary
    if summary is None:
        raise ValueError("生成最终 Markdown 前必须存在 AI 总结")
    title = transcription.source.name.rsplit(".", 1)[0]
    metadata = (
        f"- 来源：`{transcription.source.name}`\n"
        f"- 语言：`{transcription.language}`\n"
        f"- Whisper：`{transcription.engine}/{transcription.model}`\n"
        "- 总结方式：`Agent 子任务`"
    )
    transcript_text = render_transcript(
        transcription,
        timestamps=timestamps,
        pause_seconds=pause_seconds,
        max_chars=max_chars,
    )
    return (
        f"# {title}\n\n"
        f"{metadata}\n\n"
        f"## AI 摘要\n\n{summary.summary}\n\n"
        f"## 关键结论\n\n{_render_list(summary.key_points, empty='未识别到明确结论。')}\n\n"
        f"## 待办事项\n\n{_render_actions(summary)}\n\n"
        "## 完整转写\n\n"
        f"{transcript_text}\n"
    )


def _render_list(items: list[str], *, empty: str) -> str:
    return "\n".join(f"- {item}" for item in items) if items else empty


def _render_actions(summary: RecordingSummary) -> str:
    if not summary.action_items:
        return "未识别到明确待办事项。"
    lines: list[str] = []
    for item in summary.action_items:
        details = []
        if item.owner:
            details.append(f"负责人：{item.owner}")
        if item.due:
            details.append(f"截止时间：{item.due}")
        suffix = f"（{'；'.join(details)}）" if details else ""
        lines.append(f"- {item.task}{suffix}")
    return "\n".join(lines)
