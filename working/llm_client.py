from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class LLMConfigError(RuntimeError):
    """Raised when the LLM client cannot be configured from the environment."""


class LLMRequestError(RuntimeError):
    """Raised when the LLM provider returns an unusable response."""


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str
    api_key: str
    model: str
    timeout_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "OpenAICompatibleConfig":
        api_key = os.getenv("BSW_LLM_API_KEY", "").strip()
        model = os.getenv("BSW_LLM_MODEL", "").strip()
        base_url = os.getenv("BSW_LLM_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
        timeout_raw = os.getenv("BSW_LLM_TIMEOUT_SECONDS", "60").strip()

        missing = []
        if not api_key:
            missing.append("BSW_LLM_API_KEY")
        if not model:
            missing.append("BSW_LLM_MODEL")
        if missing:
            raise LLMConfigError("缺少大模型配置：" + "、".join(missing))

        try:
            timeout_seconds = float(timeout_raw)
        except ValueError as exc:
            raise LLMConfigError("BSW_LLM_TIMEOUT_SECONDS 必须是数字") from exc

        return cls(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )


class OpenAICompatibleClient:
    def __init__(self, config: OpenAICompatibleConfig):
        self.config = config

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.2, max_tokens: int = 3000) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMRequestError(f"模型接口返回 HTTP {exc.code}: {body[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise LLMRequestError(f"无法连接大模型接口：{exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise LLMRequestError("模型接口返回的不是有效 JSON") from exc

        try:
            content = response_data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMRequestError(f"模型接口响应缺少 choices[0].message.content：{response_data}") from exc

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                elif isinstance(item, str):
                    parts.append(item)
            content = "\n".join(parts)

        content = str(content).strip()
        if not content:
            raise LLMRequestError("模型返回内容为空")
        return content
