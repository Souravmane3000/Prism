"""Isolated mutation workspace for Apply Patch and Rollback.

Serialized PrismState (repo_url, baseline_sha, current_patch) is enough to
reconstruct after HITL pauses; the sandbox is not kept alive.
"""

from __future__ import annotations

import logging

from backend.config import settings
from backend.state import StructuredPatch
from backend.workspace import LocalGitWorkspace

logger = logging.getLogger(__name__)


def use_modal_sandbox() -> bool:
    return settings.environment == "production"


def open_mutation_workspace(
    repo_url: str,
    token: str,
    baseline_sha: str,
) -> LocalGitWorkspace:
    """Create a fresh isolated clone at baseline_sha."""
    logger.info("Opening mutation workspace at baseline (modal=%s)", use_modal_sandbox())
    return LocalGitWorkspace.clone(repo_url, token, baseline_sha)


def reconstruct_applied_workspace(
    repo_url: str,
    token: str,
    baseline_sha: str,
    patch: StructuredPatch,
) -> LocalGitWorkspace:
    """Clone at baseline and materialize the current cumulative patch."""
    from backend.patch.apply import materialize_patch

    workspace = open_mutation_workspace(repo_url, token, baseline_sha)
    materialize_patch(workspace, patch)
    return workspace
