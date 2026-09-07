from __future__ import annotations

import tomllib
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

from recapit.errors import ConfigurationError
from recapit.models import TimestampMode

ALLOWED_TIMESTAMP_MODES = ("none", "paragraph", "segment")


@dataclass(frozen=True, slots=True)
class AppConfig:
    engine: str = "faster-whisper"
    whisper_model: str = "turbo"
    language: str | None = "zh"
    device: str = "auto"
    compute_type: str = "int8"
    chunk_seconds: float = 900.0
    chunk_overlap_seconds: float = 10.0
    beam_size: int = 5
    vad_filter: bool = True
    hotwords: tuple[str, ...] = ()
    output_dir: Path = Path("outputs")
    timestamps: TimestampMode = "paragraph"
    paragraph_pause_seconds: float = 2.0
    paragraph_max_chars: int = 240
    overwrite: bool = False

    def validate(self) -> AppConfig:
        if self.engine != "faster-whisper":
            raise ConfigurationError("engine 仅支持 faster-whisper")
        if not self.whisper_model.strip():
            raise ConfigurationError("Whisper 模型不能为空")
        if self.chunk_seconds <= 0:
            raise ConfigurationError("chunk_seconds 必须大于 0")
        if self.chunk_overlap_seconds < 0 or self.chunk_overlap_seconds >= self.chunk_seconds:
            raise ConfigurationError("chunk_overlap_seconds 必须大于等于 0 且小于 chunk_seconds")
        if self.beam_size <= 0:
            raise ConfigurationError("beam_size 必须大于 0")
        if self.timestamps not in ALLOWED_TIMESTAMP_MODES:
            allowed = "|".join(ALLOWED_TIMESTAMP_MODES)
            raise ConfigurationError(f"timestamps 必须是 {allowed} 之一")
        if self.paragraph_pause_seconds < 0:
            raise ConfigurationError("paragraph_pause_seconds 不能小于 0")
        if self.paragraph_max_chars <= 0:
            raise ConfigurationError("字符数限制必须大于 0")
        return self


def _flatten_config(data: dict[str, Any]) -> dict[str, Any]:
    transcription = data.get("transcription", {})
    output = data.get("output", {})
    if "summary" in data:
        raise ConfigurationError("[summary] 配置已移除；总结由 Agent 子任务完成")
    if not all(isinstance(section, dict) for section in (transcription, output)):
        raise ConfigurationError("recapit.toml 的配置分区必须是 TOML 表")
    chunking = transcription.get("chunking", {})
    decode = transcription.get("decode", {})
    if not all(isinstance(section, dict) for section in (chunking, decode)):
        raise ConfigurationError("transcription.chunking/decode 必须是 TOML 表")
    return {
        "engine": transcription.get("engine"),
        "whisper_model": transcription.get("model"),
        "language": transcription.get("language"),
        "device": transcription.get("device"),
        "compute_type": transcription.get("compute_type"),
        "chunk_seconds": chunking.get("seconds", transcription.get("chunk_seconds")),
        "chunk_overlap_seconds": chunking.get(
            "overlap_seconds", transcription.get("chunk_overlap_seconds")
        ),
        "beam_size": decode.get("beam_size", transcription.get("beam_size")),
        "vad_filter": decode.get("vad_filter", transcription.get("vad_filter")),
        "hotwords": decode.get("hotwords", transcription.get("hotwords")),
        "output_dir": output.get("directory"),
        "timestamps": output.get("timestamps"),
        "paragraph_pause_seconds": output.get("paragraph_pause_seconds"),
        "paragraph_max_chars": output.get("paragraph_max_chars"),
    }


def load_config(
    config_path: Path | None = None, *, overrides: dict[str, Any] | None = None
) -> AppConfig:
    config = AppConfig()
    path = config_path or Path("recapit.toml")
    values: dict[str, Any] = {}
    if path.exists():
        try:
            with path.open("rb") as stream:
                values.update(
                    {
                        key: value
                        for key, value in _flatten_config(tomllib.load(stream)).items()
                        if value is not None
                    }
                )
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigurationError(f"无法读取配置文件 {path}: {exc}") from exc
    if overrides:
        values.update({key: value for key, value in overrides.items() if value is not None})

    valid_fields = {item.name for item in fields(AppConfig)}
    unknown = values.keys() - valid_fields
    if unknown:
        raise ConfigurationError(f"未知配置项: {', '.join(sorted(unknown))}")
    if "output_dir" in values:
        values["output_dir"] = Path(values["output_dir"])
    if values.get("language") == "auto":
        values["language"] = None
    if "hotwords" in values:
        raw_hotwords = values["hotwords"]
        if not isinstance(raw_hotwords, list) or not all(
            isinstance(item, str) for item in raw_hotwords
        ):
            raise ConfigurationError("hotwords 必须是字符串数组")
        values["hotwords"] = tuple(item.strip() for item in raw_hotwords if item.strip())
    return replace(config, **values).validate()
