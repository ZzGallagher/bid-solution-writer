#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Unified LLM API integration point.

This is the only place where a real model provider should be wired in.
All agents that need model output must call ``call_llm_api(payload)`` here
instead of reading API keys, SDK clients, URLs, or environment variables in
their own files.

Expected return shapes depend on ``payload["task"]``:

- ``generate_content_block``: return ``{"content": ["paragraph 1", ...]}``,
  a list of strings, or a plain string.
- Mermaid generation: return ``{"diagrams": [...]}`` following the Mermaid
  Agent contract included in the payload.
"""

from __future__ import annotations

from typing import Any


class LLMAPIUnavailable(NotImplementedError):
    """Raised when the unified LLM API integration has not been filled."""


def call_llm_api(payload: dict[str, Any]) -> dict[str, Any] | list[str] | str:
    """Call the configured LLM provider.

    Production integration belongs here and only here. Keep provider-specific
    API keys, URLs, SDK setup, request retries, and response parsing inside this
    function or small private helpers in this module.
    """

    raise LLMAPIUnavailable(
        "Fill the only LLM API integration point in "
        "working/agents/llm_client.py::call_llm_api(payload)."
    )
