from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"
TimestampMode = Literal["none", "paragraph", "segment"]


class Segment(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_interval(self) -> Segment:
        if self.end < self.start:
            raise ValueError("segment end must be greater than or equal to start")
        cleaned = self.text.strip()
        if not cleaned:
            raise ValueError("segment text must not be blank")
        object.__setattr__(self, "text", cleaned)
        return self


class ActionItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: str = Field(min_length=1)
    owner: str | None = None
    due: str | None = None


class RecordingSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)


class SummaryDocument(RecordingSummary):
    schema_version: str = SCHEMA_VERSION
    transcript_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def as_summary(self) -> RecordingSummary:
        return RecordingSummary(
            summary=self.summary,
            key_points=self.key_points,
            action_items=self.action_items,
        )


class SourceInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    name: str
    size_bytes: int = Field(ge=0)
    duration_seconds: float = Field(ge=0)


class Transcription(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = SCHEMA_VERSION
    source: SourceInfo
    language: str
    engine: str
    model: str
    processed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    segments: list[Segment]
    transcript_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    summary_provider: str | None = None
    summary_model: str | None = None
    recording_summary: RecordingSummary | None = None

    @model_validator(mode="after")
    def validate_segments(self) -> Transcription:
        if not self.segments:
            raise ValueError("transcription must contain at least one segment")
        starts = [segment.start for segment in self.segments]
        if starts != sorted(starts):
            raise ValueError("segments must be ordered by start time")
        expected_hash = self.calculate_sha256()
        if self.transcript_sha256 is None:
            object.__setattr__(self, "transcript_sha256", expected_hash)
        elif self.transcript_sha256 != expected_hash:
            raise ValueError("transcript_sha256 does not match segment text")
        return self

    def plain_text(self) -> str:
        return "\n".join(segment.text for segment in self.segments)

    def calculate_sha256(self) -> str:
        return hashlib.sha256(self.plain_text().encode("utf-8")).hexdigest()

    def with_summary(
        self, summary: RecordingSummary, *, provider: str = "agent", model: str | None = None
    ) -> Transcription:
        return self.model_copy(
            update={
                "recording_summary": summary,
                "summary_provider": provider,
                "summary_model": model,
            }
        )

    @classmethod
    def for_source(
        cls,
        path: Path,
        *,
        duration_seconds: float,
        language: str,
        engine: str,
        model: str,
        segments: list[Segment],
    ) -> Transcription:
        resolved = path.resolve()
        return cls(
            source=SourceInfo(
                path=str(resolved),
                name=resolved.name,
                size_bytes=resolved.stat().st_size,
                duration_seconds=duration_seconds,
            ),
            language=language,
            engine=engine,
            model=model,
            segments=segments,
        )
