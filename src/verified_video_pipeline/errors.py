"""Domain errors with stable, user-facing messages."""


class PipelineError(RuntimeError):
    """Base class for expected fail-closed pipeline errors."""


class ContractError(PipelineError):
    """Input or state violates the versioned pipeline contract."""


class IntegrityError(PipelineError):
    """An exact-byte identity changed after it was approved."""


class LeaseBusy(PipelineError):
    """Another operation already holds a fail-fast lease."""


class RecoveryRequired(PipelineError):
    """A journal cannot be recovered without operator review."""
