class RecapitError(Exception):
    """Base class for errors safe to display to CLI users."""


class ConfigurationError(RecapitError):
    """Configuration is missing or invalid."""


class MediaValidationError(RecapitError):
    """The recording cannot be read or decoded."""


class TranscriptionError(RecapitError):
    """Whisper transcription failed or returned no speech."""


class SummaryError(RecapitError):
    """AI summarization failed."""


class ArtifactError(RecapitError):
    """An output artifact cannot be safely written or loaded."""


class WordExportError(ArtifactError):
    """Word export failed after the base artifacts were safely written."""

    def __init__(self, message: str, *, markdown_path: str, json_path: str) -> None:
        super().__init__(message)
        self.markdown_path = markdown_path
        self.json_path = json_path
