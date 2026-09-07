from __future__ import annotations

import math
import re
from dataclasses import dataclass

from recapit.models import RecordingSummary, Segment, TimestampMode, Transcription

_CJK = re.compile(r"[\u3400-\u9fff]")
_SENTENCE_ENDINGS = ("。", "！", "？", ".", "!", "?")
_CLAUSE_ENDINGS = ("，", "、", "；", "：", ",", ";", ":")
_CLOSING = ("”", "’", "」", "』", "）", ")", "】", "》")
_LEADING_PUNCT = "".join(_SENTENCE_ENDINGS + _CLAUSE_ENDINGS + ("…",))
_ASCII_PUNCT_TO_CJK = str.maketrans(
    {
        ",": "，",
        ";": "；",
        ":": "：",
        "?": "？",
        "!": "！",
    }
)
_QUESTION_ENDINGS = ("吗",)
_SENTENCE_START = re.compile(
    r"^(然后呢|那么|所以|但是|不过|另外|首先|其次|最后|因此|其实|好的|对啊|嗯+)"
)


@dataclass(frozen=True, slots=True)
class Paragraph:
    start: float
    end: float
    text: str
    segments: tuple[Segment, ...]
    speaker: str | None = None


def format_timestamp(seconds: float) -> str:
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("时间秒数必须是非负有限值")
    total = math.floor(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"[{hours:02d}:{minutes:02d}:{secs:02d}]"


def normalize_transcript_text(text: str) -> str:
    value = text.strip()
    if value and _CJK.search(value):
        return value.translate(_ASCII_PUNCT_TO_CJK)
    return value


def _last_content_char(text: str) -> str:
    index = len(text) - 1
    while index >= 0 and text[index] in _CLOSING:
        index -= 1
    return text[index] if index >= 0 else ""


def _ends_with_punctuation(text: str) -> bool:
    last = _last_content_char(text)
    return last in _SENTENCE_ENDINGS or last in _CLAUSE_ENDINGS


def _connector(left: str, right: str, gap: float, *, period_gap: float) -> str:
    if not right or _ends_with_punctuation(left) or right[0] in _LEADING_PUNCT:
        return ""
    if left.endswith(_QUESTION_ENDINGS):
        return "？"
    if gap >= period_gap or _SENTENCE_START.match(right):
        return "。"
    cjk_join = bool(_CJK.search(left[-1]) or _CJK.search(right[0]))
    return "，" if cjk_join else " "


def _join_text(left: str, right: str, *, gap: float = 0.0, period_gap: float = 0.8) -> str:
    right = normalize_transcript_text(right)
    if not left:
        return right
    if not right:
        return left
    connector = _connector(left, right, gap, period_gap=period_gap)
    if connector == " ":
        return f"{left} {right}"
    return left + connector + right


def _finish_paragraph(text: str) -> str:
    if not text:
        return text
    if text.endswith("，"):
        return text[:-1] + "。"
    if _CJK.search(text) and not _ends_with_punctuation(text):
        return text + ("？" if text.endswith(_QUESTION_ENDINGS) else "。")
    return text


def merge_paragraphs(
    segments: list[Segment], *, pause_seconds: float = 2.0, max_chars: int = 240
) -> list[Paragraph]:
    if not segments:
        return []
    paragraphs: list[Paragraph] = []
    current: list[Segment] = []
    text = ""
    period_gap = min(0.8, pause_seconds) if pause_seconds else 0.8
    for segment in segments:
        piece = normalize_transcript_text(segment.text)
        if not piece:
            continue
        gap = segment.start - current[-1].end if current else 0.0
        combined = _join_text(text, piece, gap=gap, period_gap=period_gap)
        punctuation_break = bool(
            current and text.endswith(_SENTENCE_ENDINGS) and len(text) >= max_chars // 2
        )
        speaker_break = bool(current and current[-1].speaker != segment.speaker)
        if current and (
            gap > pause_seconds or len(combined) > max_chars or punctuation_break or speaker_break
        ):
            paragraphs.append(
                Paragraph(
                    current[0].start,
                    current[-1].end,
                    _finish_paragraph(text),
                    tuple(current),
                    current[0].speaker,
                )
            )
            current = [segment]
            text = piece
            continue
        current.append(segment)
        text = combined
    if current:
        paragraphs.append(
            Paragraph(
                current[0].start,
                current[-1].end,
                _finish_paragraph(text),
                tuple(current),
                current[0].speaker,
            )
        )
    return paragraphs


def _transcript_line(
    start: float, text: str, *, timestamps: TimestampMode, speaker: str | None
) -> str:
    speaker_prefix = f"{speaker}  " if speaker else ""
    if timestamps == "none":
        return f"{speaker_prefix}{text}" if speaker_prefix else text
    return f"{format_timestamp(start)} {speaker_prefix}{text}"


def render_transcript(
    transcription: Transcription,
    *,
    timestamps: TimestampMode,
    pause_seconds: float,
    max_chars: int,
) -> str:
    if timestamps == "segment":
        return "\n\n".join(
            _transcript_line(
                segment.start,
                _finish_paragraph(normalize_transcript_text(segment.text)),
                timestamps=timestamps,
                speaker=segment.speaker,
            )
            for segment in transcription.segments
        )
    paragraphs = merge_paragraphs(
        transcription.segments, pause_seconds=pause_seconds, max_chars=max_chars
    )
    if timestamps in {"paragraph", "none"}:
        return "\n\n".join(
            _transcript_line(
                paragraph.start,
                paragraph.text,
                timestamps=timestamps,
                speaker=paragraph.speaker,
            )
            for paragraph in paragraphs
        )
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
