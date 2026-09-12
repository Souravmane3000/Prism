"""backend/patch/spec.py — LLM-facing ModificationSpec."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Replacement(BaseModel):
    old: str
    new: str


class FileChange(BaseModel):
    path: str
    operation: Literal["add", "modify", "delete", "rename"] = "modify"
    old_path: str | None = None
    replacements: list[Replacement] = Field(default_factory=list)
    new_content: str | None = None


class ModificationSpec(BaseModel):
    files: list[FileChange] = Field(default_factory=list)
    summary: str = ""
