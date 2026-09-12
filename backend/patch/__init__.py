"""Patch package public surface."""

from backend.patch.apply import apply_patch, materialize_patch
from backend.patch.generate import (
    generate_cumulative_structured_patch,
    generate_structured_patch,
)
from backend.patch.rollback import rollback_to_baseline
from backend.patch.validate import validate_structured_patch

__all__ = [
    "apply_patch",
    "generate_cumulative_structured_patch",
    "generate_structured_patch",
    "materialize_patch",
    "rollback_to_baseline",
    "validate_structured_patch",
]