from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from recapit.errors import MediaValidationError, TranscriptionError
from recapit.media import validate_recording
from recapit.progress import TranscriptionStage
from recapit.transcribe import ZH_INITIAL_PROMPT, FasterWhisperTranscriber


def test_validate_recording_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.m4a"
    with pytest.raises(MediaValidationError, match=str(missing)):
        validate_recording(missing)


def test_validate_recording_requires_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "audio.m4a"
    recording.write_bytes(b"fake")
    monkeypatch.setattr("recapit.media.shutil.which", lambda _name: None)
    with pytest.raises(MediaValidationError, match="ffmpeg"):
        validate_recording(recording)


def test_validate_recording_probes_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "audio.m4a"
    recording.write_bytes(b"fake")
    monkeypatch.setattr("recapit.media.shutil.which", lambda name: f"/bin/{name}")
    completed = SimpleNamespace(returncode=0, stdout='{"format":{"duration":"12.5"}}', stderr="")
    monkeypatch.setattr("recapit.media.subprocess.run", lambda *args, **kwargs: completed)
    assert validate_recording(recording) == 12.5


def test_validate_recording_reports_decode_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "broken.m4a"
    recording.write_bytes(b"fake")
    monkeypatch.setattr("recapit.media.shutil.which", lambda name: f"/bin/{name}")
    completed = SimpleNamespace(returncode=1, stdout="", stderr="Invalid data")
    monkeypatch.setattr("recapit.media.subprocess.run", lambda *args, **kwargs: completed)
    with pytest.raises(MediaValidationError, match="Invalid data"):
        validate_recording(recording)


class FakeWhisperModel:
    last_kwargs: dict[str, object] = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def transcribe(self, _path: str, **kwargs: object) -> tuple[list[SimpleNamespace], object]:
        type(self).last_kwargs = kwargs
        return (
            [
                SimpleNamespace(start=2.0, end=3.0, text=" 后一句 "),
                SimpleNamespace(start=0.0, end=1.0, text=" 第一句 "),
                SimpleNamespace(start=1.0, end=1.2, text="  "),
            ],
            SimpleNamespace(language="zh"),
        )


def test_faster_whisper_normalizes_and_sorts(tmp_path: Path) -> None:
    recording = tmp_path / "audio.m4a"
    recording.write_bytes(b"fake")
    transcriber = FasterWhisperTranscriber(
        model="small", language="zh", model_factory=FakeWhisperModel
    )
    result = transcriber.transcribe(recording, duration_seconds=4.0)
    assert [item.text for item in result.segments] == ["第一句", "后一句"]
    assert result.model == "small"
    assert result.language == "zh"
    assert FakeWhisperModel.last_kwargs["initial_prompt"] == ZH_INITIAL_PROMPT


def test_faster_whisper_reuses_model_across_chunks(tmp_path: Path) -> None:
    recording = tmp_path / "audio.m4a"
    recording.write_bytes(b"fake")
    calls = {"count": 0}

    def factory(*args: object, **kwargs: object) -> FakeWhisperModel:
        calls["count"] += 1
        return FakeWhisperModel(*args, **kwargs)

    transcriber = FasterWhisperTranscriber(model="small", language="zh", model_factory=factory)
    transcriber.transcribe_chunk(recording, duration_seconds=4)
    transcriber.transcribe_chunk(recording, duration_seconds=4)
    assert calls["count"] == 1


def test_faster_whisper_rejects_empty_speech(tmp_path: Path) -> None:
    class EmptyModel(FakeWhisperModel):
        def transcribe(self, _path: str, **_kwargs: object) -> tuple[list[object], object]:
            return [], SimpleNamespace(language="zh")

    recording = tmp_path / "audio.m4a"
    recording.write_bytes(b"fake")
    transcriber = FasterWhisperTranscriber(model="tiny", language=None, model_factory=EmptyModel)
    with pytest.raises(TranscriptionError, match="未检测"):
        transcriber.transcribe(recording, duration_seconds=1.0)


def test_faster_whisper_emits_progress_before_generator_finishes(tmp_path: Path) -> None:
    events: list[object] = []
    finished = {"value": False}

    class StreamingModel:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def transcribe(
            self, _path: str, **_kwargs: object
        ) -> tuple[Iterator[SimpleNamespace], object]:
            def generate() -> Iterator[SimpleNamespace]:
                yield SimpleNamespace(start=0.0, end=4.0, text="第一句")
                assert any(
                    getattr(event, "stage", None) is TranscriptionStage.transcribing
                    and getattr(event, "segment_count", 0) >= 1
                    for event in events
                )
                yield SimpleNamespace(start=4.0, end=8.0, text="第二句")
                finished["value"] = True

            return generate(), SimpleNamespace(language="zh")

    recording = tmp_path / "audio.m4a"
    recording.write_bytes(b"fake")
    transcriber = FasterWhisperTranscriber(
        model="small", language="zh", model_factory=StreamingModel
    )
    result = transcriber.transcribe(recording, duration_seconds=10.0, progress=events.append)
    assert finished["value"] is True
    assert [item.text for item in result.segments] == ["第一句", "第二句"]
    stages = [event.stage for event in events]
    assert TranscriptionStage.model_loading in stages
    assert stages[-1] is TranscriptionStage.transcribing
    assert events[-1].percent == 100.0
    assert events[-1].processed_seconds == 10.0
