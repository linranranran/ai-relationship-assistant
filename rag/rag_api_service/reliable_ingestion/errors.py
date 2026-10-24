class PermanentError(ValueError):
    """A controlled, safe-to-display contract or content error; do not retry."""


class DependencyPending(RuntimeError):
    """A valid activation is waiting for its immutable creation event to arrive."""
