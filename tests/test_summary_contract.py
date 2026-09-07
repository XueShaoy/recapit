import pytest
from pydantic import ValidationError

from recapit.models import SummaryDocument


def test_summary_document_requires_hash_and_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SummaryDocument(summary="摘要", transcript_sha256="0" * 63)
    with pytest.raises(ValidationError):
        SummaryDocument(summary="摘要", transcript_sha256="0" * 64, unexpected="拒绝")
