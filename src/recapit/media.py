from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from recapit.errors import MediaValidationError


def validate_recording_path(path: Path) -> None:
    """Validate cheap local path invariants without decoding the recording."""
    if not path.exists():
        raise MediaValidationError(f"录音文件不存在: {path}")
    if not path.is_file():
        raise MediaValidationError(f"录音路径不是普通文件: {path}")
    if not os.access(path, os.R_OK):
        raise MediaValidationError(f"录音文件不可读: {path}")


def validate_recording(path: Path) -> float:
    """Validate an input recording and return its duration in seconds."""
    validate_recording_path(path)
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise MediaValidationError("缺少 ffmpeg/ffprobe，请先安装 ffmpeg")

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise MediaValidationError(f"无法启动 ffprobe: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "未知解码错误"
        raise MediaValidationError(f"录音无法解码 {path}: {detail}")
    try:
        duration = float(json.loads(completed.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaValidationError(f"无法读取录音时长: {path}") from exc
    if duration <= 0:
        raise MediaValidationError(f"录音时长必须大于 0: {path}")
    return duration
