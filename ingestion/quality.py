"""Data-quality failures are safe to log by class, never by source payload."""


class DataQualityError(Exception):
    """Source identities or immutable attributes contradict each other."""


class CheckpointError(DataQualityError):
    """Persisted progress cannot be trusted; do not advance it automatically."""
