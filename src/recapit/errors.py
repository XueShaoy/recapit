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
