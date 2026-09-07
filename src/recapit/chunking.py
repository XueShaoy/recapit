from __future__ import annotations

import contextlib
import math
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from recapit.errors import MediaValidationError, TranscriptionError
from recapit.models import Segment, WordTiming
from recapit.progress import ProgressEvent


class ChunkSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(ge=0)
    core_start: float = Field(ge=0)
    core_end: float = Field(gt=0)
    extract_start: float = Field(ge=0)
    extract_end: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_ranges(self) -> ChunkSpec:
        if not (self.extract_start <= self.core_start < self.core_end <= self.extract_end):
            raise ValueError("chunk ranges must contain a non-empty core")
        return self

    @property
    def core_duration(self) -> float:
        return self.core_end - self.core_start

    @property
    def extract_duration(self) -> float:
        return self.extract_end - self.extract_start


def build_chunk_plan(
    duration_seconds: float, *, chunk_seconds: float = 900.0, overlap_seconds: float = 10.0
) -> list[ChunkSpec]:
    if duration_seconds <= 0:
        return []
    if chunk_seconds <= 0 or overlap_seconds < 0 or overlap_seconds >= chunk_seconds:
        raise ValueError("invalid chunk plan parameters")
    count = math.ceil(duration_seconds / chunk_seconds)
    return [
        ChunkSpec(
            index=index,
            core_start=index * chunk_seconds,
            core_end=min((index + 1) * chunk_seconds, duration_seconds),
            extract_start=max(index * chunk_seconds - overlap_seconds, 0.0),
            extract_end=min((index + 1) * chunk_seconds + overlap_seconds, duration_seconds),
        )
        for index in range(count)
    ]


@contextlib.contextmanager
def extracted_chunk(source: Path, spec: ChunkSpec, directory: Path) -> Iterator[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=f".chunk-{spec.index:04d}-", suffix=".wav", delete=False
        ) as stream:
            temporary = Path(stream.name)
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{spec.extract_start:.6f}",
            "-t",
            f"{spec.extract_duration:.6f}",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(temporary),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "未知 ffmpeg 错误"
            raise MediaValidationError(f"无法提取 chunk {spec.index}: {detail}")
        yield temporary
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def absolute_segments(spec: ChunkSpec, segments: list[Segment]) -> list[Segment]:
    result: list[Segment] = []
    for segment in segments:
        start = min(max(spec.extract_start + segment.start, 0.0), spec.extract_end)
        end = min(max(spec.extract_start + segment.end, start), spec.extract_end)
        result.append(Segment(start=start, end=end, text=segment.text, speaker=segment.speaker))
    return result


def absolute_words(spec: ChunkSpec, words: list[WordTiming]) -> list[WordTiming]:
    result: list[WordTiming] = []
    for word in words:
        start = min(max(spec.extract_start + word.start, 0.0), spec.extract_end)
        end = min(max(spec.extract_start + word.end, start), spec.extract_end)
        result.append(WordTiming(start=start, end=end, text=word.text))
    return result


def merge_chunk_segments(
    chunks: list[tuple[ChunkSpec, list[Segment]]], *, overlap_tolerance: float = 1.0
) -> list[Segment]:
    owned: list[tuple[int, Segment]] = []
    for spec, segments in chunks:
        for segment in segments:
            midpoint = (segment.start + segment.end) / 2.0
            is_last_edge = math.isclose(midpoint, spec.core_end) and math.isclose(
                spec.core_end, spec.extract_end
            )
            if spec.core_start <= midpoint < spec.core_end or is_last_edge:
                owned.append((spec.index, segment))
    owned.sort(key=lambda item: (item[1].start, item[1].end, item[0]))
    result: list[Segment] = []
    for _index, segment in owned:
        if result and segment.text == result[-1].text and segment.start <= result[-1].end:
            continue
        if result and segment.start + overlap_tolerance < result[-1].start:
            raise TranscriptionError("chunk 合并出现时间倒退")
        if result and segment.start < result[-1].end - 30.0:
            raise TranscriptionError("chunk 边界存在无法安全消解的显著重叠")
        result.append(segment)
    return result


def merge_chunk_words(
    chunks: list[tuple[ChunkSpec, list[WordTiming]]], *, overlap_tolerance: float = 1.0
) -> list[WordTiming]:
    merged = merge_chunk_segments(
        [
            (
                spec,
                [Segment(start=word.start, end=word.end, text=word.text) for word in words],
            )
            for spec, words in chunks
        ],
        overlap_tolerance=overlap_tolerance,
    )
    return [WordTiming(start=item.start, end=item.end, text=item.text) for item in merged]


def aggregate_chunk_progress(
    event: ProgressEvent,
    spec: ChunkSpec,
    *,
    completed_core_seconds: float,
    total_seconds: float,
) -> ProgressEvent:
    relative_core = min(
        max(event.processed_seconds - (spec.core_start - spec.extract_start), 0.0),
        spec.core_duration,
    )
    processed = min(completed_core_seconds + relative_core, total_seconds)
    percent = 0.0 if total_seconds <= 0 else min(processed / total_seconds * 100.0, 99.0)
    return ProgressEvent(
        stage=event.stage,
        duration_seconds=total_seconds,
        processed_seconds=processed,
        segment_count=event.segment_count,
        elapsed_seconds=event.elapsed_seconds,
        percent=percent,
        eta_seconds=event.eta_seconds,
        latest_text=event.latest_text,
        heartbeat=event.heartbeat,
    )
