"""Recapit recording transcription and summarization workflow."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("recapit")
except PackageNotFoundError:  # pragma: no cover - editable source without metadata
    __version__ = "0.0.0"

__all__ = ["__version__"]
