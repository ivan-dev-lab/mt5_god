"""Project-specific exceptions."""


class Mt5GodError(Exception):
    """Base exception for the application."""


class ConfigurationError(Mt5GodError):
    """Raised when application configuration is invalid."""


class PipelineTransitionError(Mt5GodError):
    """Raised when a task performs an invalid pipeline transition."""


class DuplicateEventError(Mt5GodError):
    """Raised when an incoming capture event is already processed."""


class AnalysisError(Mt5GodError):
    """Raised when analyzer fails to produce a valid result."""


class ExecutionError(Mt5GodError):
    """Raised when executor fails to complete a trade."""


class UiAutomationError(ExecutionError):
    """Raised when the MT5 UI layer cannot verify an action."""
