from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


class LLMAPIUnavailable(RuntimeError):
    pass


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = Path(os.environ.get("BID_SOLUTION_ENV_FILE", WORKSPACE_ROOT / ".env"))
API_LOG_DIR = Path("working") / "api-logs"
API_KEY_PLACEHOLDERS = {"", "your-api-key-here", "在这里粘贴你的 DeepSeek API Key"}


def call_llm_api(payload: dict[str, Any]) -> dict[str, Any]:
    _load_env_file(ENV_FILE)
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    model = os.environ.get("DEEPSEEK_MODEL", "").strip()
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
    timeout = int(os.environ.get("DEEPSEEK_TIMEOUT_SECONDS", "120"))
    if api_key in API_KEY_PLACEHOLDERS:
        raise LLMAPIUnavailable(f"未配置 DEEPSEEK_API_KEY，请在 {ENV_FILE} 或环境变量中配置。")
    if not model:
        raise LLMAPIUnavailable(f"未配置 DEEPSEEK_MODEL，请在 {ENV_FILE} 或环境变量中配置。")

    body = {
        "model": model,
        "messages": _build_messages(payload),
        "response_format": {"type": "json_object"},
        "stream": False,
        "temperature": 0.2,
        "max_tokens": 8192,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    url = f"{base_url.rstrip('/')}/chat/completions"
    log_record: dict[str, Any] = {
        "call_id": str(uuid.uuid4()),
        "started_at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "task": payload.get("task"),
        "url": url,
        "request": {"headers": {"Authorization": "Bearer ***REDACTED***"}, "body": body},
    }
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        content = response_data["choices"][0]["message"]["content"]
        parsed = _parse_json_content(content)
        log_record["finished_at"] = datetime.now().astimezone().isoformat(timespec="milliseconds")
        log_record["parsed_output"] = parsed
        _write_api_log(log_record)
        return parsed
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        log_record["error"] = {"type": "HTTPError", "status": exc.code, "body": detail}
        _write_api_log(log_record)
        raise RuntimeError(f"DeepSeek API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        log_record["error"] = {"type": "URLError", "reason": str(exc.reason)}
        _write_api_log(log_record)
        raise RuntimeError(f"无法连接 DeepSeek API：{exc.reason}") from exc
    except Exception as exc:
        log_record["error"] = {"type": type(exc).__name__, "reason": str(exc)}
        _write_api_log(log_record)
        raise


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _build_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": "你是投标方案生成系统的结构化输出模块。必须只返回合法 JSON，不要输出 Markdown 代码块或解释文字。",
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _parse_json_content(content: str) -> dict[str, Any]:
    candidate = content.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.DOTALL | re.IGNORECASE)
    if match:
        candidate = match.group(1).strip()
    return json.loads(candidate)


def _write_api_log(record: dict[str, Any]) -> None:
    path = Path(os.environ.get("BID_SOLUTION_API_LOG", API_LOG_DIR / f"external-api-{datetime.now().astimezone():%Y%m%d}.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    record.setdefault("finished_at", datetime.now().astimezone().isoformat(timespec="milliseconds"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
