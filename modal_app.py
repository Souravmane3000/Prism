"""
modal_app.py — Modal deployment wrapper for the Prism FastAPI backend.

Deployment:
  modal deploy modal_app.py

Local development:
  modal serve modal_app.py

The Modal Sandbox instances used by test_runner.py are created at runtime
inside the agent node — this file only defines the API server function.

Production secrets are sourced from the Modal Secret named "prism-secrets".
In development, Modal picks up local environment variables automatically.
"""

import modal

# ── Modal App ─────────────────────────────────────────────────────────────────
app = modal.App("prism")

# ── Container image ───────────────────────────────────────────────────────────
# Install all runtime dependencies from pyproject.toml.
# System packages: git is needed by the Modal.Sandbox git clone operations
# that are spawned by test_runner.py at runtime inside separate sandboxes.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")
    .pip_install_from_pyproject("pyproject.toml")
    .copy_local_dir("backend", "/root/backend")
)

# ── Secrets ───────────────────────────────────────────────────────────────────
# In production: all env vars come from this Modal Secret.
# In local development: Modal picks up .env variables via python-dotenv in config.py.
production_secrets = modal.Secret.from_name("prism-secrets")


# ── Web endpoint ──────────────────────────────────────────────────────────────
@app.function(
    image=image,
    secrets=[production_secrets],
    timeout=600,       # agents can be slow on large repos; 10 min ceiling
    memory=1024,       # MB — sufficient for embedding + LLM calls in-process
    min_containers=1,  # keep one container warm to avoid cold-start on first demo request
)
@modal.asgi_app()
def web() -> object:
    """
    Entry point for the Modal ASGI web endpoint.

    Returns the FastAPI ASGI app. Modal calls this function once per container
    start and then routes all HTTP requests through the returned app.
    """
    # Import here (not at module level) so Modal builds the container image
    # without executing application startup code during the image build phase.
    from backend.main import app as fastapi_app  # noqa: PLC0415

    return fastapi_app
