"""pipeline package — merge, confidence, projection, validation."""
from .merger     import merge_records
from .confidence import compute_confidence
from .projector  import project
from .validator  import validate_output

__all__ = ["merge_records", "compute_confidence", "project", "validate_output"]