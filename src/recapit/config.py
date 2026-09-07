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
    output_dir: Path = Path("outputs")
    timestamps: TimestampMode = "paragraph"
    paragraph_pause_seconds: float = 2.0
    paragraph_max_chars: int = 240
    overwrite: bool = False

    def validate(self) -> AppConfig:
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
    return {
        "engine": transcription.get("engine"),
        "whisper_model": transcription.get("model"),
        "language": transcription.get("language"),
        "device": transcription.get("device"),
        "compute_type": transcription.get("compute_type"),
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
    return replace(config, **values).validate()
