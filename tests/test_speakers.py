from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from test_artifacts_formatter import transcription as base_transcription
from test_chunking_state import IndexedSession, indexed_extractor

from recapit.config import AppConfig, load_config
from recapit.errors import ArtifactError, ConfigurationError
from recapit.formatter import merge_paragraphs, render_markdown, render_transcript
from recapit.identity import run_signature, speaker_signature
from recapit.models import RecordingSummary, Segment, Transcription, WordTiming
from recapit.run_state import (
    SpeakerStage,
    SpeakerTurn,
    WorkflowStage,
    load_manifest,
    write_manifest,
)
from recapit.speakers import (
    TOKEN_ENV_NAMES,
    PyannoteDiarizer,
    apply_speaker_labels,
    exclusive_turns_from_output,
    extracted_diarization_wav,
    huggingface_token,
    label_map_from_turns,
    pipeline_kwargs,
)
from recapit.workflow import transcribe_recording


def test_huggingface_token_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    assert huggingface_token() is None
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "legacy-token")
    assert huggingface_token() == "legacy-token"
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", "hub-token")
    assert huggingface_token() == "hub-token"
    monkeypatch.setenv("HF_TOKEN", "standard-token")
    assert huggingface_token() == "standard-token"
    for name in TOKEN_ENV_NAMES:
        assert name


def test_config_rejects_invalid_max_speakers() -> None:
    with pytest.raises(ConfigurationError, match="max_speakers"):
        AppConfig(max_speakers=1).validate()
    with pytest.raises(ConfigurationError, match="num_speakers"):
        AppConfig(num_speakers=1).validate()


def test_speakers_toml_section(tmp_path: Path) -> None:
    config_file = tmp_path / "recapit.toml"
    config_file.write_text(
        """[transcription.speakers]
enabled = true
max_speakers = 3
num_speakers = 2
""",
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config.speakers is True
    assert config.max_speakers == 3
    assert config.num_speakers == 2


def test_whisper_signature_ignores_speakers_and_display() -> None:
    base = AppConfig()
    assert run_signature(base, actual_device="cpu") == run_signature(
        AppConfig(speakers=True, max_speakers=3, timestamps="none"),
        actual_device="cpu",
    )
    assert speaker_signature(AppConfig(speakers=True)) != speaker_signature(
        AppConfig(speakers=True, max_speakers=3)
    )


def test_segment_speaker_optional_round_trip() -> None:
    labeled = Segment(start=0, end=1, text="你好", speaker="A")
    restored = Segment.model_validate_json(labeled.model_dump_json())
    assert restored.speaker == "A"
    old = Segment.model_validate_json('{"start":0,"end":1,"text":"你好"}')
    assert old.speaker is None


def test_label_map_orders_by_first_speech() -> None:
    turns = [
        SpeakerTurn(start=4.0, end=6.0, speaker_id="SPEAKER_01"),
        SpeakerTurn(start=0.5, end=2.0, speaker_id="SPEAKER_00"),
        SpeakerTurn(start=8.0, end=9.0, speaker_id="SPEAKER_01"),
    ]
    mapping = label_map_from_turns(turns)
    assert mapping == {"SPEAKER_00": "A", "SPEAKER_01": "B"}
    restored = label_map_from_turns(turns)
    assert restored == mapping


def test_word_midpoint_split_across_two_speakers() -> None:
    words = [
        WordTiming(start=0.0, end=0.4, text="你好"),
        WordTiming(start=0.4, end=0.8, text="啊"),
        WordTiming(start=0.8, end=1.2, text="请问"),
        WordTiming(start=1.2, end=1.6, text="什么"),
    ]
    turns = [
        SpeakerTurn(start=0.0, end=0.8, speaker_id="SPEAKER_00"),
        SpeakerTurn(start=0.8, end=2.0, speaker_id="SPEAKER_01"),
    ]
    segments, mapping = apply_speaker_labels(
        segments=[Segment(start=0.0, end=1.6, text="你好啊请问什么")],
        words=words,
        turns=turns,
    )
    assert mapping["SPEAKER_00"] == "A"
    assert [item.speaker for item in segments] == ["A", "B"]
    assert len(segments) >= 2


def test_pipeline_kwargs_prefer_exact_count() -> None:
    assert pipeline_kwargs(AppConfig()) == {"max_speakers": 4}
    assert pipeline_kwargs(AppConfig(num_speakers=2, max_speakers=4)) == {"num_speakers": 2}


def test_exclusive_turns_from_output() -> None:
    class Turn:
        def __init__(self, start: float, end: float) -> None:
            self.start = start
            self.end = end

    class Annotation:
        def itertracks(self, yield_label: bool = False):
            yield Turn(1.0, 2.0), None, "SPEAKER_01"
            yield Turn(0.0, 1.0), None, "SPEAKER_00"

    turns = exclusive_turns_from_output(SimpleNamespace(exclusive_speaker_diarization=Annotation()))
    assert [item.speaker_id for item in turns] == ["SPEAKER_00", "SPEAKER_01"]


def test_extracted_wav_uses_16khz_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "audio.m4a"
    source.write_bytes(b"fake")
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
        seen["command"] = command
        Path(command[-1]).write_bytes(b"wav")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("recapit.speakers.subprocess.run", fake_run)
    leftover: list[Path] = []
    with extracted_diarization_wav(source, tmp_path) as wav:
        leftover.append(wav)
        assert wav.exists()
    assert not leftover[0].exists()
    command = seen["command"]
    assert isinstance(command, list)
    assert "-ar" in command and "16000" in command
    assert "-ac" in command and "1" in command


def test_missing_token_names_gated_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    monkeypatch.setattr("recapit.speakers.speaker_pipeline_cached", lambda: False)
    monkeypatch.setattr("recapit.speakers.require_speakers_extra", lambda: None)

    class Factory:
        def __call__(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("should not download without token")

    diarizer = PyannoteDiarizer(pipeline_factory=Factory())
    with pytest.raises(ConfigurationError, match="HF_TOKEN") as caught:
        diarizer._load()
    assert "secret" not in str(caught.value)
    assert "HF_TOKEN" in str(caught.value)


def test_gated_401_mentions_agreement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_TOKEN", "secret-token-value")
    monkeypatch.setattr("recapit.speakers.speaker_pipeline_cached", lambda: False)
    monkeypatch.setattr("recapit.speakers.require_speakers_extra", lambda: None)

    def factory(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("401 gated repository")

    diarizer = PyannoteDiarizer(pipeline_factory=factory)
    with pytest.raises(ConfigurationError, match="同意条款") as caught:
        diarizer._load()
    assert "secret-token-value" not in str(caught.value)


class FakeDiarizer:
    def __init__(self, turns: list[SpeakerTurn] | None = None) -> None:
        self.calls: list[dict[str, int]] = []
        self.turns = turns or [
            SpeakerTurn(start=0.0, end=5.0, speaker_id="SPEAKER_00"),
            SpeakerTurn(start=5.0, end=10.0, speaker_id="SPEAKER_01"),
        ]

    def diarize(self, _wav: Path, *, config: AppConfig) -> list[SpeakerTurn]:
        options = pipeline_kwargs(config)
        self.calls.append(options)
        return self.turns


@contextmanager
def fake_wav(source: Path, directory: Path) -> Iterator[Path]:
    path = directory / ".speakers-test.wav"
    path.write_bytes(b"wav")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def test_speakers_missing_extra_does_not_write_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "talk.m4a"
    recording.write_bytes(b"audio")
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: 2.0)
    monkeypatch.setattr(
        "recapit.workflow.require_speakers_extra",
        lambda: (_ for _ in ()).throw(ConfigurationError("说话人分离需要可选依赖")),
    )
    current: dict[str, object] = {}
    with pytest.raises(ConfigurationError, match="可选依赖"):
        transcribe_recording(
            recording,
            AppConfig(output_dir=tmp_path, speakers=True),
            transcriber=IndexedSession(current),  # type: ignore[arg-type]
            chunk_extractor=indexed_extractor(current),  # type: ignore[arg-type]
        )
    assert not list(tmp_path.glob("*/speakers.json"))


def test_word_timestamps_only_when_speakers_enabled(tmp_path: Path) -> None:
    from recapit.transcribe import FasterWhisperTranscriber

    class Model:
        last: dict[str, object] = {}

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def transcribe(self, _path: str, **kwargs: object):
            type(self).last = kwargs
            word = SimpleNamespace(start=0.0, end=0.4, word="你好")
            return (
                [SimpleNamespace(start=0.0, end=1.0, text="你好", words=[word])],
                SimpleNamespace(language="zh"),
            )

    recording = tmp_path / "a.m4a"
    recording.write_bytes(b"x")
    off = FasterWhisperTranscriber(model="small", language="zh", model_factory=Model)
    off.transcribe_chunk(recording, duration_seconds=1)
    assert Model.last["word_timestamps"] is False
    on = FasterWhisperTranscriber(
        model="small", language="zh", model_factory=Model, word_timestamps=True
    )
    _segments, _language, words = on.transcribe_chunk(recording, duration_seconds=1)
    assert Model.last["word_timestamps"] is True
    assert words[0].text == "你好"


class WordSession(IndexedSession):
    def transcribe_chunk(self, path: Path, *, duration_seconds: float, progress: object):
        segments, language = super().transcribe_chunk(
            path, duration_seconds=duration_seconds, progress=progress
        )
        spec = self.current["spec"]
        start = spec.core_start - spec.extract_start + 1
        words = [
            WordTiming(start=start, end=start + 0.4, text="你好"),
            WordTiming(start=start + 0.4, end=start + 1.0, text="请问"),
        ]
        return segments, language, words


def test_workflow_labels_speakers_and_reuses_whisper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "meet.m4a"
    recording.write_bytes(b"audio")
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: 2.0)
    current: dict[str, object] = {}
    first = WordSession(current)
    diarizer = FakeDiarizer(
        [
            SpeakerTurn(start=0.0, end=1.2, speaker_id="SPEAKER_00"),
            SpeakerTurn(start=1.2, end=3.0, speaker_id="SPEAKER_01"),
        ]
    )
    result = transcribe_recording(
        recording,
        AppConfig(output_dir=tmp_path, speakers=True),
        transcriber=first,  # type: ignore[arg-type]
        chunk_extractor=indexed_extractor(current),  # type: ignore[arg-type]
        speaker_diarizer=diarizer,  # type: ignore[arg-type]
        speaker_wav_extractor=fake_wav,
    )
    assert {segment.speaker for segment in result.transcription.segments} <= {"A", "B"}
    assert all(segment.speaker for segment in result.transcription.segments)
    manifest = load_manifest(result.paths.run_manifest)
    assert manifest.speaker_stage is SpeakerStage.complete
    assert result.paths.speakers_json.exists()
    assert first.calls == [0]

    resumed = WordSession(current)
    again = FakeDiarizer(diarizer.turns)
    transcribe_recording(
        recording,
        AppConfig(output_dir=tmp_path, speakers=True, max_speakers=3),
        transcriber=resumed,  # type: ignore[arg-type]
        chunk_extractor=indexed_extractor(current),  # type: ignore[arg-type]
        speaker_diarizer=again,  # type: ignore[arg-type]
        speaker_wav_extractor=fake_wav,
    )
    assert resumed.calls == []
    assert again.calls == [{"max_speakers": 3}]


def test_default_chunk_checkpoint_omits_word_timings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "talk.m4a"
    recording.write_bytes(b"audio")
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: 2.0)
    current: dict[str, object] = {}
    result = transcribe_recording(
        recording,
        AppConfig(output_dir=tmp_path, speakers=False),
        transcriber=WordSession(current),  # type: ignore[arg-type]
        chunk_extractor=indexed_extractor(current),  # type: ignore[arg-type]
    )
    from recapit.run_state import load_chunk_checkpoint

    checkpoint = load_chunk_checkpoint(next((result.paths.chunks_directory).glob("*.json")))
    assert checkpoint.words == []


def test_speakers_json_commits_before_transcript_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "meet.m4a"
    recording.write_bytes(b"audio")
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: 2.0)
    current: dict[str, object] = {}
    original = transcribe_recording.__globals__["write_transcript_json"]

    def fail_transcript(
        path: Path, transcription: Transcription, *, replace_existing: bool
    ) -> None:
        if path.name == "transcript.json":
            raise ArtifactError("injected transcript failure")
        original(path, transcription, replace_existing=replace_existing)

    monkeypatch.setattr("recapit.workflow.write_transcript_json", fail_transcript)
    with pytest.raises(ArtifactError, match="injected"):
        transcribe_recording(
            recording,
            AppConfig(output_dir=tmp_path, speakers=True),
            transcriber=IndexedSession(current),  # type: ignore[arg-type]
            chunk_extractor=indexed_extractor(current),  # type: ignore[arg-type]
            speaker_diarizer=FakeDiarizer(),  # type: ignore[arg-type]
            speaker_wav_extractor=fake_wav,
        )
    speakers = next(tmp_path.glob("*/speakers.json"))
    assert speakers.exists()


def test_workflow_labels_speakers_after_rendered_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recording = tmp_path / "meet.m4a"
    recording.write_bytes(b"audio")
    monkeypatch.setattr("recapit.workflow.validate_recording", lambda _path: 2.0)
    current: dict[str, object] = {}
    first = transcribe_recording(
        recording,
        AppConfig(output_dir=tmp_path, speakers=False),
        transcriber=WordSession(current),  # type: ignore[arg-type]
        chunk_extractor=indexed_extractor(current),  # type: ignore[arg-type]
    )
    write_manifest(
        first.paths.run_manifest,
        load_manifest(first.paths.run_manifest).with_stage(WorkflowStage.rendered),
    )
    labeled = transcribe_recording(
        recording,
        AppConfig(output_dir=tmp_path, speakers=True),
        transcriber=WordSession(current),  # type: ignore[arg-type]
        chunk_extractor=indexed_extractor(current),  # type: ignore[arg-type]
        speaker_diarizer=FakeDiarizer(
            [
                SpeakerTurn(start=0.0, end=1.2, speaker_id="SPEAKER_00"),
                SpeakerTurn(start=1.2, end=3.0, speaker_id="SPEAKER_01"),
            ]
        ),  # type: ignore[arg-type]
        speaker_wav_extractor=fake_wav,
    )
    manifest = load_manifest(labeled.paths.run_manifest)
    assert manifest.stage is WorkflowStage.rendered
    assert manifest.speaker_stage is SpeakerStage.complete
    assert all(segment.speaker for segment in labeled.transcription.segments)


def test_merge_paragraphs_breaks_on_speaker_change() -> None:
    paragraphs = merge_paragraphs(
        [
            Segment(start=0.0, end=1.0, text="你好", speaker="A"),
            Segment(start=1.1, end=2.0, text="请问", speaker="B"),
            Segment(start=2.1, end=3.0, text="我再补充", speaker="B"),
        ],
        pause_seconds=2.0,
        max_chars=240,
    )
    assert [item.speaker for item in paragraphs] == ["A", "B"]
    assert paragraphs[1].text.startswith("请问")


def test_render_transcript_speaker_prefixes() -> None:
    value = Transcription(
        source=base_transcription().source,
        language="zh",
        engine="faster-whisper",
        model="small",
        segments=[
            Segment(start=0.9, end=1.5, text="今天讨论目标。", speaker="A"),
            Segment(start=5.1, end=6.0, text="最后安排任务。", speaker="B"),
        ],
    )
    paragraph = render_transcript(value, timestamps="paragraph", pause_seconds=2, max_chars=240)
    none = render_transcript(value, timestamps="none", pause_seconds=2, max_chars=240)
    segment = render_transcript(value, timestamps="segment", pause_seconds=2, max_chars=240)
    assert "[00:00:00] A  " in paragraph
    assert none.startswith("A  ")
    assert "[" not in none
    assert "[00:00:00] A  " in segment
    unlabeled = render_transcript(
        base_transcription(), timestamps="paragraph", pause_seconds=2, max_chars=240
    )
    assert " A  " not in unlabeled


def test_word_keeps_speaker_prefix(tmp_path: Path) -> None:
    from recapit.word import markdown_to_docx

    value = Transcription(
        source=base_transcription().source,
        language="zh",
        engine="faster-whisper",
        model="small",
        segments=[
            Segment(start=0.9, end=1.5, text="今天讨论目标。", speaker="A"),
            Segment(start=5.1, end=6.0, text="最后安排任务。", speaker="B"),
        ],
    ).with_summary(RecordingSummary(summary="摘要", key_points=["结论"], action_items=[]))
    markdown = render_markdown(value, timestamps="paragraph", pause_seconds=2, max_chars=240)
    target = tmp_path / "out.docx"
    markdown_to_docx(markdown, target, replace_existing=False)
    text = "\n".join(paragraph.text for paragraph in Document(target).paragraphs)
    assert "[00:00:00] A  " in text
    assert "[00:00:05] B  " in text
    assert "摘要" in text
