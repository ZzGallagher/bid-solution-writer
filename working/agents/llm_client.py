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

import json
import os
import re
import uuid
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


DEEPSEEK_API_KEY = "sk-01ff3e91d2074b3ba93da4d516bab6aa"
DEEPSEEK_MODEL = "deepseek-v4-flash"  # 可选：deepseek-v4-flash / deepseek-v4-pro
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_TIMEOUT_SECONDS = int(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "120"))
DEFAULT_API_LOG_DIR = Path("working") / "agent-system" / "api-logs"


class LLMAPIUnavailable(NotImplementedError):
    """Raised when the unified LLM API integration has not been filled."""


def call_llm_api(payload: dict[str, Any]) -> dict[str, Any] | list[str] | str:
    """Call the configured LLM provider.

    Production integration belongs here and only here. Keep provider-specific
    API keys, URLs, SDK setup, request retries, and response parsing inside this
    function or small private helpers in this module.
    """

    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY == "在这里粘贴你的 DeepSeek API Key":
        raise LLMAPIUnavailable("Please fill DEEPSEEK_API_KEY in working/agents/llm_client.py.")

    request_body = {
        "model": DEEPSEEK_MODEL,
        "messages": _build_messages(payload),
        "response_format": {"type": "json_object"},
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 8192,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
    }
    call_id = str(uuid.uuid4())
    started_at = datetime.now().astimezone().isoformat(timespec="milliseconds")
    log_record: dict[str, Any] = {
        "call_id": call_id,
        "started_at": started_at,
        "provider": "deepseek",
        "url": f"{DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions",
        "task": payload.get("task"),
        "agent": payload.get("agent"),
        "request": {
            "headers": _redact_headers(headers),
            "body": request_body,
        },
    }
    request = urllib.request.Request(
        log_record["url"],
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=DEEPSEEK_TIMEOUT_SECONDS) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        log_record["finished_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        log_record["error"] = {"type": "HTTPError", "status": exc.code, "body": detail}
        _write_api_log(log_record)
        raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        log_record["finished_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        log_record["error"] = {"type": "URLError", "reason": str(exc.reason)}
        _write_api_log(log_record)
        raise RuntimeError(f"Unable to connect to DeepSeek API: {exc.reason}") from exc
    except TimeoutError as exc:
        log_record["finished_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        log_record["error"] = {"type": "TimeoutError", "reason": str(exc)}
        _write_api_log(log_record)
        raise RuntimeError(f"DeepSeek API request timed out: {exc}") from exc
    except Exception as exc:
        log_record["finished_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        log_record["error"] = {"type": type(exc).__name__, "reason": str(exc)}
        _write_api_log(log_record)
        raise

    try:
        content = response_data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        log_record["finished_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        log_record["response"] = response_data
        log_record["error"] = {"type": "UnexpectedResponseShape"}
        _write_api_log(log_record)
        raise RuntimeError(f"Unexpected DeepSeek API response: {response_data}") from exc

    if not content:
        log_record["finished_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        log_record["response"] = response_data
        log_record["error"] = {"type": "EmptyContent"}
        _write_api_log(log_record)
        raise RuntimeError("DeepSeek API returned empty content.")

    try:
        parsed, parsed_with_repair = _parse_json_content(content)
    except json.JSONDecodeError as exc:
        log_record["finished_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        log_record["response"] = response_data
        log_record["error"] = {"type": "InvalidJSONContent", "content": content}
        _write_api_log(log_record)
        raise RuntimeError(f"DeepSeek API did not return valid JSON: {content}") from exc
    log_record["finished_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
    log_record["response"] = response_data
    log_record["parsed_output"] = parsed
    log_record["parsed_with_repair"] = parsed_with_repair
    _write_api_log(log_record)
    return parsed


def _parse_json_content(content: str) -> tuple[Any, bool]:
    candidate = _strip_markdown_fence(content.strip())
    try:
        return json.loads(candidate), False
    except json.JSONDecodeError:
        sanitized = _sanitize_mermaid_edge_label_quotes(candidate)
        if sanitized != candidate:
            try:
                return json.loads(sanitized), True
            except json.JSONDecodeError:
                candidate = sanitized
        repaired = _repair_missing_final_string_quote(candidate)
        if repaired != candidate:
            return json.loads(repaired), True
        raise


def _strip_markdown_fence(content: str) -> str:
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return content


def _sanitize_mermaid_edge_label_quotes(content: str) -> str:
    return re.sub(r'\|"([^"\n\r]+)"\|', r"|\1|", content)


def _repair_missing_final_string_quote(content: str) -> str:
    if _has_even_unescaped_quotes(content):
        return content
    if not re.search(r"\n\s*\]\s*\}\s*$", content):
        return content
    return re.sub(r"\n(\s*\]\s*\}\s*)$", r'"\n\1', content, count=1)


def _has_even_unescaped_quotes(content: str) -> bool:
    count = 0
    escaped = False
    for char in content:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            count += 1
    return count % 2 == 0


def _api_log_path() -> Path:
    configured = os.environ.get("BID_SOLUTION_API_LOG")
    if configured:
        return Path(configured)
    stamp = datetime.now().astimezone().strftime("%Y%m%d")
    return DEFAULT_API_LOG_DIR / f"external-api-{stamp}.jsonl"


def _write_api_log(record: dict[str, Any]) -> None:
    path = _api_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted = dict(headers)
    if "Authorization" in redacted:
        redacted["Authorization"] = "Bearer ***REDACTED***"
    return redacted


def _build_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    task = str(payload.get("task") or "")
    if task == "generate_content_block":
        output_example = '{"content": ["段落1", "段落2"]}'
    elif task == "generate_design_blueprint":
        output_example = (
            '{"architecture_layers": [{"layer_id": "L001", "name": "数据接入层", '
            '"responsibility": "...", "source_requirement_ids": ["T001"]}], '
            '"modules": [{"module_id": "M001", "name": "模块", "responsibility": "...", '
            '"layer_ids": ["L001"], "source_requirement_ids": ["T001"], '
            '"related_scoring_item_ids": []}], '
            '"sections": [{"section_id": "SEC001", "placeholder": "【GEN:总体架构设计】", '
            '"title": "系统总体架构", "content_type": "generated_paragraphs", '
            '"source_requirement_ids": ["T001"], "related_scoring_item_ids": [], '
            '"writing_requirement_ids": [], "module_ids": [], "status": "planned"}], '
            '"diagram_plan": [{"diagram_id": "DG001", "title": "系统总体架构图", '
            '"kind": "architecture", "purpose": "...", "layout_hint": "flowchart TB", '
            '"source_requirement_ids": ["T001"], "related_section_ids": [], '
            '"related_module_ids": ["M001"]}]}'
        )
    else:
        output_example = (
            '{"diagrams": [{"diagram_id": "DG001", "description": "...", '
            '"mermaid": "flowchart TB\\nA[开始] --> B[结束]", '
            '"node_trace": [], "review_notes": []}]}'
        )

    return [
        {
            "role": "system",
            "content": (
                "你是投标方案生成系统的结构化输出模块。"
                "必须严格返回合法 JSON，不要使用 Markdown 代码块，不要输出解释文字。"
                f"JSON 返回格式示例：{output_example}"
            ),
        },
        {
            "role": "user",
            "content": (
                "请根据以下 JSON payload 生成结果，并只返回 JSON：\n"
                + json.dumps(payload, ensure_ascii=False)
            ),
        },
    ]
