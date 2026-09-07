from __future__ import annotations

import contextlib
import math
import os
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from recapit.config import AppConfig
from recapit.errors import ConfigurationError, MediaValidationError, TranscriptionError
from recapit.formatter import _join_text, normalize_transcript_text
from recapit.identity import SPEAKER_PIPELINE_ID
from recapit.models import Segment, WordTiming
from recapit.run_state import SpeakerCheckpoint, SpeakerTurn
from recapit.transcribe import huggingface_hub_dir

TOKEN_ENV_NAMES = ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN")
SPEAKER_MODEL_PAGE = f"https://huggingface.co/{SPEAKER_PIPELINE_ID}"
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

SpeakerDiarizer = Callable[..., list[SpeakerTurn]]


def huggingface_token() -> str | None:
    for name in TOKEN_ENV_NAMES:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def require_speakers_extra() -> None:
    import importlib.util

    if importlib.util.find_spec("pyannote.audio") is None:
        raise ConfigurationError("说话人分离需要可选依赖，请运行 uv sync --extra speakers")


def speaker_pipeline_cached() -> bool:
    snapshots = (
        huggingface_hub_dir() / f"models--{SPEAKER_PIPELINE_ID.replace('/', '--')}" / "snapshots"
    )
    try:
        if not snapshots.is_dir():
            return False
        return any(snapshot.is_dir() for snapshot in snapshots.iterdir())
    except OSError:
        return False


def label_map_from_turns(turns: list[SpeakerTurn]) -> dict[str, str]:
    first_seen: dict[str, float] = {}
    for turn in turns:
        previous = first_seen.get(turn.speaker_id)
        first_seen[turn.speaker_id] = turn.start if previous is None else min(previous, turn.start)
    ordered = sorted(first_seen, key=lambda speaker_id: (first_seen[speaker_id], speaker_id))
    mapping: dict[str, str] = {}
    for index, speaker_id in enumerate(ordered):
        mapping[speaker_id] = _LETTERS[index] if index < len(_LETTERS) else f"S{index + 1}"
    return mapping


def pipeline_kwargs(config: AppConfig) -> dict[str, int]:
    if config.num_speakers is not None:
        return {"num_speakers": config.num_speakers}
    return {"max_speakers": config.max_speakers}


def speaker_at(time: float, turns: list[SpeakerTurn], label_map: dict[str, str]) -> str:
    for turn in turns:
        if turn.start <= time < turn.end or (math.isclose(time, turn.end) and time >= turn.start):
            label = label_map.get(turn.speaker_id)
            if label:
                return label
    if not turns:
        return "A"

    def distance(turn: SpeakerTurn) -> float:
        if turn.start <= time <= turn.end:
            return 0.0
        return min(abs(time - turn.start), abs(time - turn.end))

    nearest = min(turns, key=distance)
    return label_map.get(nearest.speaker_id, "A")


def assign_words(
    words: list[WordTiming], turns: list[SpeakerTurn], label_map: dict[str, str]
) -> list[tuple[WordTiming, str]]:
    labeled: list[tuple[WordTiming, str]] = []
    for word in words:
        midpoint = (word.start + word.end) / 2.0
        labeled.append((word, speaker_at(midpoint, turns, label_map)))
    return labeled


def segments_from_labeled_words(
    labeled: list[tuple[WordTiming, str]],
) -> list[Segment]:
    if not labeled:
        return []
    groups: list[list[tuple[WordTiming, str]]] = [[labeled[0]]]
    for item in labeled[1:]:
        if item[1] == groups[-1][-1][1]:
            groups[-1].append(item)
        else:
            groups.append([item])
    segments: list[Segment] = []
    for group in groups:
        text = ""
        for index, (word, _speaker) in enumerate(group):
            gap = 0.0 if index == 0 else word.start - group[index - 1][0].end
            text = _join_text(text, word.text, gap=gap)
        if not text:
            continue
        segments.append(
            Segment(
                start=group[0][0].start,
                end=group[-1][0].end,
                text=text,
                speaker=group[0][1],
            )
        )
    return segments


def split_segments_by_turns(
    segments: list[Segment], turns: list[SpeakerTurn], label_map: dict[str, str]
) -> list[Segment]:
    result: list[Segment] = []
    for segment in segments:
        bounds = {segment.start, segment.end}
        for turn in turns:
            if segment.start < turn.start < segment.end:
                bounds.add(turn.start)
            if segment.start < turn.end < segment.end:
                bounds.add(turn.end)
        points = sorted(bounds)
        duration = segment.end - segment.start
        text = normalize_transcript_text(segment.text)
        if duration <= 0 or len(points) == 2:
            result.append(
                Segment(
                    start=segment.start,
                    end=segment.end,
                    text=segment.text,
                    speaker=speaker_at((segment.start + segment.end) / 2.0, turns, label_map),
                )
            )
            continue
        char_count = max(len(text), 1)
        cursor = 0
        for index in range(len(points) - 1):
            start = points[index]
            end = points[index + 1]
            if math.isclose(start, end):
                continue
            share = (end - start) / duration
            take = char_count if index == len(points) - 2 else max(1, round(share * char_count))
            piece = text[cursor : cursor + take] or text[cursor:]
            cursor += len(piece)
            cleaned = normalize_transcript_text(piece)
            if not cleaned:
                continue
            result.append(
                Segment(
                    start=start,
                    end=end,
                    text=cleaned,
                    speaker=speaker_at((start + end) / 2.0, turns, label_map),
                )
            )
        if cursor < len(text) and result:
            remainder = normalize_transcript_text(text[cursor:])
            if remainder:
                last = result[-1]
                result[-1] = Segment(
                    start=last.start,
                    end=last.end,
                    text=last.text + remainder,
                    speaker=last.speaker,
                )
    return result


def apply_speaker_labels(
    *,
    segments: list[Segment],
    words: list[WordTiming],
    turns: list[SpeakerTurn],
    label_map: dict[str, str] | None = None,
) -> tuple[list[Segment], dict[str, str]]:
    if not turns:
        raise TranscriptionError("未检测到说话人区间")
    mapping = label_map or label_map_from_turns(turns)
    if words:
        labeled_segments = segments_from_labeled_words(assign_words(words, turns, mapping))
        if labeled_segments:
            return labeled_segments, mapping
    return split_segments_by_turns(segments, turns, mapping), mapping


def build_speaker_checkpoint(
    config: AppConfig, *, signature: str, turns: list[SpeakerTurn], label_map: dict[str, str]
) -> SpeakerCheckpoint:
    return SpeakerCheckpoint(
        speaker_signature=signature,
        pipeline=SPEAKER_PIPELINE_ID,
        max_speakers=config.max_speakers,
        num_speakers=config.num_speakers,
        label_map=label_map,
        turns=turns,
    )


@contextlib.contextmanager
def extracted_diarization_wav(source: Path, directory: Path) -> Iterator[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".speakers-", suffix=".wav", delete=False
        ) as stream:
            temporary = Path(stream.name)
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
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
            raise MediaValidationError(f"无法提取说话人分离音频: {detail}")
        yield temporary
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _turns_from_annotation(annotation: Any) -> list[SpeakerTurn]:
    turns: list[SpeakerTurn] = []
    iterator = getattr(annotation, "itertracks", None)
    if callable(iterator):
        for item in iterator(yield_label=True):
            if len(item) == 3:
                turn, _track, label = item
            else:
                turn, label = item[0], item[-1]
            turns.append(
                SpeakerTurn(
                    start=float(getattr(turn, "start", 0.0)),
                    end=float(getattr(turn, "end", 0.0)),
                    speaker_id=str(label),
                )
            )
        return turns
    if isinstance(annotation, list):
        for item in annotation:
            turns.append(
                SpeakerTurn(
                    start=float(item.start),
                    end=float(item.end),
                    speaker_id=str(item.speaker_id),
                )
            )
    return turns


def exclusive_turns_from_output(output: Any) -> list[SpeakerTurn]:
    annotation = getattr(output, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(output, "speaker_diarization", output)
    turns = _turns_from_annotation(annotation)
    turns.sort(key=lambda item: (item.start, item.end, item.speaker_id))
    return turns


class PyannoteDiarizer:
    def __init__(self, *, pipeline_factory: Callable[..., Any] | None = None) -> None:
        self._pipeline_factory = pipeline_factory
        self._pipeline: Any | None = None
        self.last_call: dict[str, int] | None = None

    def _load(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline
        require_speakers_extra()
        if self._pipeline_factory is None:
            try:
                import importlib

                pipeline_mod = importlib.import_module("pyannote.audio")
                self._pipeline_factory = pipeline_mod.Pipeline.from_pretrained
            except ImportError as exc:  # pragma: no cover
                raise ConfigurationError(
                    "说话人分离需要可选依赖，请运行 uv sync --extra speakers"
                ) from exc
        token = huggingface_token()
        cached = speaker_pipeline_cached()
        if not cached and not token:
            raise ConfigurationError(
                "下载说话人模型需要 Hugging Face Token。"
                f"请设置 {' / '.join(TOKEN_ENV_NAMES)}，"
                f"并在 {SPEAKER_MODEL_PAGE} 同意条款"
            )
        try:
            kwargs: dict[str, Any] = {}
            if token:
                kwargs["token"] = token
            self._pipeline = self._pipeline_factory(SPEAKER_PIPELINE_ID, **kwargs)
        except Exception as exc:
            message = str(exc)
            if any(code in message for code in ("401", "403", "gated", "restricted", "authorized")):
                raise ConfigurationError(
                    f"当前 Token 无权下载说话人模型，请使用同一账号在 {SPEAKER_MODEL_PAGE} 同意条款"
                ) from exc
            raise TranscriptionError(f"无法加载说话人分离模型: {exc}") from exc
        return self._pipeline

    def diarize(self, wav: Path, *, config: AppConfig) -> list[SpeakerTurn]:
        pipeline = self._load()
        options = pipeline_kwargs(config)
        self.last_call = options
        try:
            output = pipeline(str(wav), **options)
        except TypeError:
            output = pipeline(str(wav))
        except Exception as exc:
            raise TranscriptionError(f"说话人分离失败: {exc}") from exc
        turns = exclusive_turns_from_output(output)
        if not turns:
            raise TranscriptionError("未检测到说话人区间")
        return turns
