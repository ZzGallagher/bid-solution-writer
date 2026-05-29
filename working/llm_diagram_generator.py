from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm_client import OpenAICompatibleClient


class DiagramGenerationError(RuntimeError):
    """Raised when a diagram cannot be generated or validated."""


@dataclass(frozen=True)
class DiagramRequest:
    key: str
    kind: str
    output_stem: str
    title: str
    context: dict[str, Any]
    examples: str = ""


@dataclass
class DiagramSpec:
    title: str
    mermaid: str
    description: str
    source_requirement_ids: list[str] = field(default_factory=list)
    review_notes: list[str] = field(default_factory=list)


def read_examples(example_dir: Path, *, max_chars: int = 12000) -> str:
    if not example_dir.exists():
        return ""

    chunks: list[str] = []
    for path in sorted(example_dir.iterdir()):
        if path.suffix.lower() not in {".mmd", ".md"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
        if text:
            chunks.append(f"## 示例：{path.name}\n{text}")

    return "\n\n".join(chunks)[:max_chars]


def build_prompts(request: DiagramRequest) -> tuple[str, str]:
    if request.kind == "architecture":
        diagram_rules = """
生成总体架构图。图中必须体现：分层关系、核心功能能力、数据资源、接口集成、安全保障、运维监控、性能/可靠性/可维护性支撑。
建议 10-25 个核心节点。可使用子图 subgraph，但不要过度复杂。
"""
    elif request.kind == "function_flow":
        diagram_rules = """
生成单个功能模块的业务/数据处理流程图。图中必须体现：业务触发、输入或状态校验、核心处理步骤、数据读写、接口交互、异常处理、日志审计、结果反馈。
不要使用通用固定六步模板，必须根据本功能模块的具体需求条款生成差异化流程。
"""
    else:
        raise DiagramGenerationError(f"未知图类型：{request.kind}")

    system_prompt = f"""
你是投标技术方案编写助手，负责根据招标文件抽取结果生成可渲染的 Mermaid 图。

硬性要求：
1. 只输出一个 JSON 对象，不要输出 Markdown、解释、代码围栏或多余文本。
2. JSON 字段必须包含 title、mermaid、description、source_requirement_ids、review_notes。
3. mermaid 字段必须是 Mermaid 源码字符串，且以 flowchart TB、flowchart TD 或 flowchart LR 开头。
4. 图中节点使用中文，文字简洁；详细解释写入 description。
5. 不要编造招标文件中没有确认的品牌、厂商、云服务商、硬件型号、人员资质或承诺。
6. source_requirement_ids 只填写上下文中出现过的需求编号。
7. Mermaid 源码不要包含 ```，不要包含 markdown 标题。

{diagram_rules.strip()}
""".strip()

    user_payload = {
        "diagram_title": request.title,
        "diagram_kind": request.kind,
        "context": request.context,
    }
    examples = request.examples.strip()
    example_text = f"\n\n参考示例仅用于约束风格和粒度，不可照抄具体节点：\n{examples}" if examples else ""

    user_prompt = f"""
请根据以下 JSON 上下文生成 {request.title}。

输出 JSON 示例：
{{
  "title": "总体架构图",
  "mermaid": "flowchart TB\\n    A[用户访问层] --> B[业务应用层]",
  "description": "本图说明……",
  "source_requirement_ids": ["T001", "T002"],
  "review_notes": []
}}

上下文：
{json.dumps(user_payload, ensure_ascii=False, indent=2)}
{example_text}
""".strip()

    return system_prompt, user_prompt


def save_prompt(path: Path, system_prompt: str, user_prompt: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Diagram Prompt\n\n"
        "## System\n\n"
        f"{system_prompt}\n\n"
        "## User\n\n"
        f"{user_prompt}\n",
        encoding="utf-8-sig",
    )


def generate_diagram_spec(
    client: OpenAICompatibleClient,
    request: DiagramRequest,
    *,
    prompt_dir: Path,
    error_dir: Path,
) -> DiagramSpec:
    system_prompt, user_prompt = build_prompts(request)
    save_prompt(prompt_dir / f"{request.output_stem}.md", system_prompt, user_prompt)

    raw = ""
    try:
        raw = client.chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=3500,
        )
        return parse_diagram_response(raw)
    except Exception as exc:
        error_dir.mkdir(parents=True, exist_ok=True)
        (error_dir / f"{request.output_stem}.md").write_text(
            "# Diagram Generation Error\n\n"
            f"- 图名称：{request.title}\n"
            f"- 图类型：{request.kind}\n"
            f"- 错误：{exc}\n\n"
            "## Raw Response\n\n"
            f"{raw}\n",
            encoding="utf-8-sig",
        )
        if isinstance(exc, DiagramGenerationError):
            raise
        raise DiagramGenerationError(str(exc)) from exc


def parse_diagram_response(raw: str) -> DiagramSpec:
    payload = _loads_json_object(raw)
    required = {"title", "mermaid", "description", "source_requirement_ids", "review_notes"}
    missing = sorted(required - set(payload))
    if missing:
        raise DiagramGenerationError("模型输出缺少字段：" + "、".join(missing))

    title = _require_str(payload["title"], "title")
    mermaid = validate_mermaid(_require_str(payload["mermaid"], "mermaid"))
    description = _require_str(payload["description"], "description")
    source_requirement_ids = _require_str_list(payload["source_requirement_ids"], "source_requirement_ids")
    review_notes = _require_str_list(payload["review_notes"], "review_notes")

    return DiagramSpec(
        title=title,
        mermaid=mermaid,
        description=description,
        source_requirement_ids=source_requirement_ids,
        review_notes=review_notes,
    )


def validate_mermaid(mermaid: str) -> str:
    text = mermaid.strip().lstrip("\ufeff")
    text = re.sub(r"^```(?:mermaid)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    if "```" in text:
        raise DiagramGenerationError("Mermaid 源码不能包含代码围栏")
    if not re.match(r"^flowchart\s+(TB|TD|LR)\b", text):
        raise DiagramGenerationError("Mermaid 源码必须以 flowchart TB、flowchart TD 或 flowchart LR 开头")
    if len(text.splitlines()) < 3:
        raise DiagramGenerationError("Mermaid 源码过短，无法形成有效图")
    return text


def _loads_json_object(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise DiagramGenerationError("模型输出不是 JSON 对象")
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise DiagramGenerationError(f"模型输出 JSON 解析失败：{exc}") from exc

    if not isinstance(payload, dict):
        raise DiagramGenerationError("模型输出必须是 JSON 对象")
    return payload


def _require_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DiagramGenerationError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _require_str_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise DiagramGenerationError(f"{field_name} 必须是字符串数组")
    result = []
    for item in value:
        if not isinstance(item, str):
            raise DiagramGenerationError(f"{field_name} 中存在非字符串元素")
        if item.strip():
            result.append(item.strip())
    return result
