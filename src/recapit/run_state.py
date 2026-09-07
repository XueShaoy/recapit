from __future__ import annotations

import json
import os
import uuid
from contextlib import AbstractContextManager
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from recapit.artifacts import atomic_write_text
from recapit.chunking import ChunkSpec
from recapit.errors import ArtifactError
from recapit.identity import sha256_file
from recapit.models import Segment

RUN_SCHEMA_VERSION = "1.0"


class WorkflowStage(StrEnum):
    planned = "planned"
    transcribing = "transcribing"
    transcribed = "transcribed"
    summary_ready = "summary-ready"
    rendered = "rendered"


class ChunkStatus(StrEnum):
    planned = "planned"
    completed = "completed"


class ChunkCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    spec: ChunkSpec
    run_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    language: str
    segments: list[Segment]


class ChunkRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    spec: ChunkSpec
    status: ChunkStatus = ChunkStatus.planned
    path: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_commit(self) -> ChunkRecord:
        if self.status is ChunkStatus.completed and (self.path is None or self.sha256 is None):
            raise ValueError("completed chunk requires path and sha256")
        return self


class ArtifactRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RunManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_size_bytes: int = Field(ge=0)
    source_duration_seconds: float = Field(gt=0)
    source_path: str
    run_signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    stage: WorkflowStage = WorkflowStage.planned
    chunks: list[ChunkRecord]
    artifacts: dict[str, ArtifactRecord] = Field(default_factory=dict)

    def with_stage(self, stage: WorkflowStage) -> RunManifest:
        order = list(WorkflowStage)
        if order.index(stage) < order.index(self.stage):
            raise ArtifactError(f"运行阶段不能从 {self.stage} 倒退到 {stage}")
        return self.model_copy(update={"stage": stage})


def load_manifest(path: Path) -> RunManifest:
    try:
        return RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ArtifactError(f"无法读取运行 manifest {path}: {exc}") from exc
    except ValidationError as exc:
        raise ArtifactError(f"运行 manifest 格式无效 {path}: {exc}") from exc


def write_manifest(path: Path, manifest: RunManifest) -> None:
    atomic_write_text(path, manifest.model_dump_json(indent=2) + "\n", replace_existing=True)


def write_chunk_checkpoint(path: Path, checkpoint: ChunkCheckpoint) -> str:
    atomic_write_text(path, checkpoint.model_dump_json(indent=2) + "\n", replace_existing=True)
    loaded = load_chunk_checkpoint(path)
    if loaded != checkpoint:
        raise ArtifactError(f"chunk 检查点回读校验失败: {path}")
    return sha256_file(path)


def load_chunk_checkpoint(path: Path, *, expected_sha256: str | None = None) -> ChunkCheckpoint:
    if expected_sha256 is not None and sha256_file(path) != expected_sha256:
        raise ArtifactError(f"chunk 检查点摘要不匹配: {path}")
    try:
        return ChunkCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ArtifactError(f"无法读取 chunk 检查点 {path}: {exc}") from exc
    except ValidationError as exc:
        raise ArtifactError(f"chunk 检查点格式无效 {path}: {exc}") from exc


class RunLock(AbstractContextManager["RunLock"]):
    def __init__(self, path: Path) -> None:
        self.path = path
        self._token = uuid.uuid4().hex

    def __enter__(self) -> RunLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "token": self._token})
        for _attempt in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if not self._reclaim_dead_lock():
                    raise ArtifactError(f"已有转写进程占用运行锁: {self.path}") from None
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            return self
        raise ArtifactError(f"无法获取运行锁: {self.path}")

    def _reclaim_dead_lock(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(payload["pid"])
            os.kill(pid, 0)
        except ProcessLookupError:
            self.path.unlink(missing_ok=True)
            return True
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return False
        return False

    def __exit__(self, *_args: object) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("token") == self._token:
                self.path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
