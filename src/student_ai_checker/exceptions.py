"""Domain-specific exceptions exposed by the technical core."""


class StudentAICheckerError(Exception):
    """Base exception for expected application-level failures."""


class DocumentExtractionError(StudentAICheckerError):
    """Raised when a source document cannot be parsed safely."""


class OllamaError(StudentAICheckerError):
    """Base error for local Ollama communication failures."""


class OllamaUnavailableError(OllamaError):
    """Raised when the local Ollama service cannot be reached."""


class OllamaModelMissingError(OllamaError):
    """Raised when the configured model is not installed in Ollama."""


class OllamaResponseError(OllamaError):
    """Raised when Ollama returns an unusable response."""


class GradingValidationError(StudentAICheckerError):
    """Raised when an AI grading response violates trusted constraints."""
