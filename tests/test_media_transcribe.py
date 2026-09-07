from pathlib import Path
from types import SimpleNamespace

import pytest

from recapit.errors import MediaValidationError, TranscriptionError
from recapit.media import validate_recording
from recapit.transcribe import FasterWhisperTranscriber


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
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def transcribe(self, _path: str, **_kwargs: object) -> tuple[list[SimpleNamespace], object]:
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


def test_faster_whisper_rejects_empty_speech(tmp_path: Path) -> None:
    class EmptyModel(FakeWhisperModel):
        def transcribe(self, _path: str, **_kwargs: object) -> tuple[list[object], object]:
            return [], SimpleNamespace(language="zh")

    recording = tmp_path / "audio.m4a"
    recording.write_bytes(b"fake")
    transcriber = FasterWhisperTranscriber(model="tiny", language=None, model_factory=EmptyModel)
    with pytest.raises(TranscriptionError, match="未检测"):
        transcriber.transcribe(recording, duration_seconds=1.0)
