"""Targeted context assembly for Code Modifier and Debugger."""

from __future__ import annotations

from dataclasses import dataclass

from backend.llm import ContextOverflowError

CODE_MODIFIER_INPUT_BUDGET = 96_000


@dataclass
class AssembledContext:
    text: str
    token_count: int


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def assemble_code_modifier_context(
    *,
    header: str,
    files: dict[str, str],
    budget: int = CODE_MODIFIER_INPUT_BUDGET,
) -> AssembledContext:
    parts = [header]
    used = _approx_tokens(header)
    for path, content in files.items():
        block = f"\n### {path}\n```\n{content}\n```\n"
        tokens = _approx_tokens(block)
        if used + tokens > budget:
            raise ContextOverflowError(
                f"Required context for {path} exceeds the {budget} token budget"
            )
        parts.append(block)
        used += tokens
    return AssembledContext(text="".join(parts), token_count=used)
