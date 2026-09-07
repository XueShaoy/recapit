from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from recapit.errors import ArtifactError
from recapit.models import SCHEMA_VERSION, SummaryDocument, Transcription


@dataclass(frozen=True, slots=True)
class ArtifactPaths:
    directory: Path
    markdown: Path
    transcript_json: Path
    transcript_text: Path
    summary_template: Path
    summary_json: Path

    @property
    def transcription_targets(self) -> tuple[Path, ...]:
        return (
            self.markdown,
            self.transcript_json,
            self.transcript_text,
            self.summary_template,
            self.summary_json,
        )


def output_paths(source: Path, output_root: Path) -> ArtifactPaths:
    stem = source.stem.strip() or "recording"
    return _paths_in_directory(output_root / stem, stem)


def _paths_in_directory(output_dir: Path, stem: str) -> ArtifactPaths:
    return ArtifactPaths(
        directory=output_dir,
        markdown=output_dir / f"{stem}.md",
        transcript_json=output_dir / "transcript.json",
        transcript_text=output_dir / "transcript.txt",
        summary_template=output_dir / "summary.template.json",
        summary_json=output_dir / "summary.json",
    )


def checkpoint_paths(transcript_json: Path) -> ArtifactPaths:
    transcription = load_transcript_json(transcript_json)
    stem = Path(transcription.source.path).stem.strip() or "recording"
    return _paths_in_directory(transcript_json.parent, stem)


def ensure_targets_available(paths: tuple[Path, ...], *, overwrite: bool) -> None:
    existing = [path for path in paths if path.exists()]
    if existing and not overwrite:
        joined = ", ".join(str(path) for path in existing)
        raise ArtifactError(f"目标产物已存在: {joined}；请使用 --overwrite 或其他输出目录")


def atomic_write_text(path: Path, content: str, *, replace_existing: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace_existing:
        raise ArtifactError(f"目标产物已存在: {path}")
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
        raise ArtifactError(f"无法写入产物 {path}: {exc}") from exc


def write_transcript_json(
    path: Path, transcription: Transcription, *, replace_existing: bool
) -> None:
    payload = transcription.model_dump_json(indent=2) + "\n"
    atomic_write_text(path, payload, replace_existing=replace_existing)


def load_transcript_json(path: Path) -> Transcription:
    try:
        return Transcription.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ArtifactError(f"无法读取转写检查点 {path}: {exc}") from exc
    except ValidationError as exc:
        raise ArtifactError(f"转写检查点格式无效 {path}: {exc}") from exc


def write_summary_inputs(
    paths: ArtifactPaths,
    transcription: Transcription,
    *,
    replace_existing: bool,
) -> None:
    digest = transcription.transcript_sha256
    assert digest is not None
    template = {
        "schema_version": SCHEMA_VERSION,
        "transcript_sha256": digest,
        "summary": "",
        "key_points": [],
        "action_items": [],
    }
    atomic_write_text(
        paths.transcript_text,
        transcription.plain_text() + "\n",
        replace_existing=replace_existing,
    )
    atomic_write_text(
        paths.summary_template,
        json.dumps(template, ensure_ascii=False, indent=2) + "\n",
        replace_existing=replace_existing,
    )


def load_summary_json(path: Path) -> SummaryDocument:
    try:
        return SummaryDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ArtifactError(f"无法读取 Agent 总结 {path}: {exc}") from exc
    except ValidationError as exc:
        raise ArtifactError(f"Agent 总结格式无效 {path}: {exc}") from exc
