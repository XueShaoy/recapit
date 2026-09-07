from pathlib import Path

import pytest
from pydantic import ValidationError

from recapit.models import SummaryDocument


def test_summary_document_requires_hash_and_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SummaryDocument(summary="摘要", transcript_sha256="0" * 63)
    with pytest.raises(ValidationError):
        SummaryDocument(summary="摘要", transcript_sha256="0" * 64, unexpected="拒绝")


def test_recording_skill_describes_resume_and_immutable_outputs() -> None:
    skill = (
        Path(__file__).parents[1] / ".agents" / "skills" / "recording-recap" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "15 分钟核心区间" in skill
    assert "--restart" in skill
    assert "不可变的 `transcript.json`" in skill
    assert "`recap.json`" in skill
    assert "只启动一个子 Agent" in skill
    assert "不得将音频或转写文字发送给外部 API" in skill
    assert "--speakers" in skill
    assert "真实姓名" in skill
