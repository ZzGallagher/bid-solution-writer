#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Mermaid Agent.

This stage consumes Design Agent diagram planning and asks an LLM to generate
Mermaid source for each planned diagram. It writes diagram specs and .mmd files
only; PNG rendering and diagram-manifest creation belong to Render Validate.

The model call is routed through ``llm_client.call_llm_api``. Fill the actual
LLM API call only in ``working/agents/llm_client.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_client import call_llm_api


AGENT_NAME = "Mermaid Agent"
AGENT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"

REQ_ID_RE = re.compile(r"^(T|P|Q|B|D)[0-9]{3}$")
DIAGRAM_ID_RE = re.compile(r"^DG[0-9]{3}$")
SECTION_ID_RE = re.compile(r"^SEC[0-9]{3}$")
MODULE_ID_RE = re.compile(r"^M[0-9]{3}$")
NODE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,31}$")
FLOWCHART_RE = re.compile(r"^\s*flowchart\s+(TB|TD)\b", re.IGNORECASE)
DEFAULT_LLM_BATCH_SIZE = int(os.environ.get("MERMAID_LLM_BATCH_SIZE", "6"))
MIN_PRIMARY_FUNCTION_FLOW_DIAGRAMS = 5

ALLOWED_KINDS = {
    "architecture",
    "business_flow",
    "data_flow",
    "function_flow",
    "deployment",
    "security",
    "sequence",
    "other",
}




class MermaidAgent:
    def __init__(
        self,
        workspace: Path,
        records_dir: Path,
        output_dir: Path,
        allow_local_draft: bool = False,
    ) -> None:
        self.workspace = workspace
        self.records_dir = records_dir
        self.output_dir = output_dir
        self.allow_local_draft = allow_local_draft
        now = datetime.now().astimezone()
        self.generated_at = now.isoformat(timespec="seconds")
        self.run_id = f"RUN-{now:%Y%m%d-%H%M%S}"
        self.warnings: list[str] = []

    def run(self) -> dict[str, Path]:
        requirements = self.load_json(self.records_dir / "requirements.json")
        blueprint = self.load_json(self.records_dir / "design-blueprint.json")
        diagram_plan_doc = self.load_json(self.records_dir / "diagram-plan.json")
        content_blocks = self.load_optional_json(self.records_dir / "content-blocks.json")

        self.run_id = str(diagram_plan_doc.get("run_id") or blueprint.get("run_id") or requirements.get("run_id") or self.run_id)
        diagram_plan = self.extract_diagram_plan(diagram_plan_doc, blueprint)
        self.validate_inputs(requirements, blueprint, diagram_plan_doc, diagram_plan)

        if self.allow_local_draft:
            raise RuntimeError("Mermaid Agent 已禁用 --allow-local-draft；目标图表必须通过 API 生成 Mermaid 源码。")
        else:
            diagrams = self.generate_diagrams_with_llm(requirements, blueprint, diagram_plan, content_blocks)

        staging_dir = self.workspace / "working" / "agent-system" / "staging" / "diagrams" / self.run_id
        published_dir = self.workspace / "working" / "agent-system" / "published" / "diagrams" / self.run_id
        output_diagram_dir = self.output_dir / "diagrams"
        for directory in (staging_dir, published_dir, self.output_dir, output_diagram_dir):
            directory.mkdir(parents=True, exist_ok=True)

        specs = self.build_diagram_specs(requirements, blueprint, diagram_plan_doc, diagrams, output_diagram_dir)
        self.validate_specs(specs)

        outputs: dict[str, str] = {
            "diagram-specs.json": json.dumps(specs, ensure_ascii=False, indent=2) + "\n",
            "diagram-descriptions.md": self.render_descriptions(specs),
        }
        for diagram in specs["diagrams"]:
            outputs[f"{diagram['diagram_id']}.mmd"] = diagram["mermaid"].rstrip() + "\n"

        for name, content in outputs.items():
            (staging_dir / name).write_text(content, encoding="utf-8")

        for name in outputs:
            shutil.copy2(staging_dir / name, published_dir / name)
            if name.endswith(".mmd"):
                shutil.copy2(staging_dir / name, output_diagram_dir / name)
            else:
                shutil.copy2(staging_dir / name, self.output_dir / name)

        return {"staging_dir": staging_dir, "published_dir": published_dir, "output_dir": self.output_dir}

    def load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Missing Mermaid Agent input: {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - convert to stage failure
            raise RuntimeError(f"Unable to parse JSON input: {path}") from exc

    def load_optional_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - convert to stage failure
            raise RuntimeError(f"Unable to parse JSON input: {path}") from exc

    @staticmethod
    def extract_diagram_plan(diagram_plan_doc: dict[str, Any], blueprint: dict[str, Any]) -> list[dict[str, Any]]:
        diagrams = diagram_plan_doc.get("diagrams")
        if diagrams is None:
            diagrams = blueprint.get("diagram_plan", [])
        return list(diagrams or [])

    def validate_inputs(
        self,
        requirements: dict[str, Any],
        blueprint: dict[str, Any],
        diagram_plan_doc: dict[str, Any],
        diagram_plan: list[dict[str, Any]],
    ) -> None:
        if requirements.get("artifact") != "requirements":
            raise ValueError("requirements.json artifact must be requirements")
        if blueprint.get("artifact") != "design-blueprint":
            raise ValueError("design-blueprint.json artifact must be design-blueprint")
        if diagram_plan_doc.get("artifact") != "diagram-plan":
            raise ValueError("diagram-plan.json artifact must be diagram-plan")
        if not diagram_plan:
            raise ValueError("diagram-plan.json has no diagrams")

        known_req_ids = self.collect_requirement_ids(requirements)
        known_sections = {section.get("section_id") for section in blueprint.get("sections", [])}
        known_modules = {module.get("module_id") for module in blueprint.get("modules", [])}
        seen_diagrams: set[str] = set()

        for item in diagram_plan:
            diagram_id = str(item.get("diagram_id", ""))
            if not DIAGRAM_ID_RE.match(diagram_id):
                raise ValueError(f"Invalid diagram_id: {diagram_id}")
            if diagram_id in seen_diagrams:
                raise ValueError(f"Duplicate diagram_id: {diagram_id}")
            seen_diagrams.add(diagram_id)

            if item.get("kind") not in ALLOWED_KINDS:
                raise ValueError(f"{diagram_id} has invalid kind: {item.get('kind')}")
            source_ids = item.get("source_requirement_ids", [])
            if not source_ids:
                raise ValueError(f"{diagram_id} missing source_requirement_ids")
            unknown_req_ids = [req_id for req_id in source_ids if req_id not in known_req_ids]
            if unknown_req_ids:
                raise ValueError(f"{diagram_id} references unknown requirement IDs: {', '.join(unknown_req_ids)}")

            unknown_sections = [
                section_id
                for section_id in item.get("related_section_ids", [])
                if section_id not in known_sections or not SECTION_ID_RE.match(str(section_id))
            ]
            if unknown_sections:
                raise ValueError(f"{diagram_id} references unknown section IDs: {', '.join(map(str, unknown_sections))}")

            for module_id in item.get("related_module_ids", []):
                if module_id not in known_modules or not MODULE_ID_RE.match(str(module_id)):
                    raise ValueError(f"{diagram_id} references unknown module ID: {module_id}")

        architecture_count = sum(1 for item in diagram_plan if item.get("kind") == "architecture" and ("架构" in str(item.get("title", ""))))
        function_flow_count = sum(1 for item in diagram_plan if item.get("kind") == "function_flow")
        if architecture_count < 1:
            raise ValueError("diagram-plan.json 缺少系统总体架构图。")
        if function_flow_count < MIN_PRIMARY_FUNCTION_FLOW_DIAGRAMS:
            raise ValueError(f"diagram-plan.json 一级功能流程图不足：{function_flow_count}/{MIN_PRIMARY_FUNCTION_FLOW_DIAGRAMS}")

    def generate_diagrams_with_llm(
        self,
        requirements: dict[str, Any],
        blueprint: dict[str, Any],
        diagram_plan: list[dict[str, Any]],
        content_blocks: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        batch_size = max(1, DEFAULT_LLM_BATCH_SIZE)
        diagrams: list[dict[str, Any]] = []
        total_batches = (len(diagram_plan) + batch_size - 1) // batch_size
        for batch_index, start in enumerate(range(0, len(diagram_plan), batch_size), 1):
            batch_plan = diagram_plan[start : start + batch_size]
            llm_request = self.build_llm_request(requirements, blueprint, batch_plan, content_blocks)
            llm_request["batch"] = {
                "batch_index": batch_index,
                "total_batches": total_batches,
                "diagram_ids": [item.get("diagram_id") for item in batch_plan],
            }
            try:
                llm_response = call_llm_api(llm_request)
            except NotImplementedError:
                raise
            try:
                diagrams.extend(self.normalize_llm_response(llm_response, batch_plan))
            except ValueError:
                diagrams.extend(
                    self.generate_diagrams_one_by_one(
                        requirements,
                        blueprint,
                        batch_plan,
                        content_blocks,
                        batch_index,
                        total_batches,
                    )
                )
        return diagrams

    def generate_diagrams_one_by_one(
        self,
        requirements: dict[str, Any],
        blueprint: dict[str, Any],
        diagram_plan: list[dict[str, Any]],
        content_blocks: dict[str, Any] | None,
        parent_batch_index: int,
        total_batches: int,
    ) -> list[dict[str, Any]]:
        diagrams: list[dict[str, Any]] = []
        for plan in diagram_plan:
            diagram_id = plan.get("diagram_id")
            llm_request = self.build_llm_request(requirements, blueprint, [plan], content_blocks)
            llm_request["batch"] = {
                "batch_index": parent_batch_index,
                "total_batches": total_batches,
                "retry_mode": "single_diagram",
                "diagram_ids": [diagram_id],
            }
            llm_request["rules"].append(f"Return exactly one diagram object and its diagram_id must be {diagram_id}.")
            try:
                llm_response = call_llm_api(llm_request)
            except NotImplementedError:
                raise
            diagrams.extend(self.normalize_llm_response(llm_response, [plan]))
        return diagrams

    def build_llm_request(
        self,
        requirements: dict[str, Any],
        blueprint: dict[str, Any],
        diagram_plan: list[dict[str, Any]],
        content_blocks: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        required_ids = self.unique(
            req_id
            for diagram in diagram_plan
            for req_id in diagram.get("source_requirement_ids", [])
            if REQ_ID_RE.match(str(req_id))
        )
        requirements_by_id = {item.get("requirement_id"): item for item in self.all_requirement_items(requirements)}
        sections_by_id = {section.get("section_id"): section for section in blueprint.get("sections", [])}
        modules = blueprint.get("modules", [])
        modules_by_id = {module.get("module_id"): module for module in modules}
        architecture_text = self.architecture_design_text(content_blocks or {})
        architecture_prompt = self.architecture_diagram_prompt(architecture_text)

        request = {
            "task": "Generate Mermaid source from diagram-plan.json for bid-solution design diagrams.",
            "output_contract": {
                "format": "json_object",
                "root_required": ["diagrams"],
                "diagram_required": ["diagram_id", "description", "mermaid", "node_trace", "review_notes"],
                "mermaid_default": "flowchart TB",
            },
            "rules": [
                "Only generate Mermaid source and diagram descriptions; do not write proposal prose.",
                "Use flowchart TB or flowchart TD by default. Avoid LR layouts unless a plan explicitly requires a non-flowchart diagram.",
                "Keep diagrams compact and readable for Word insertion; prefer 6 to 14 nodes per diagram.",
                "Do not invent vendors, model numbers, staff names, certificates, prices, schedules, or service commitments.",
                "Every node_trace.source_requirement_ids value must come from that diagram's source_requirement_ids.",
                "Use stable ASCII node IDs such as A, B, C1, D2; put Chinese business labels in brackets or braces.",
                "Do not use quoted Mermaid edge labels such as -->|\"label\"|; use -->|label| or omit edge labels.",
                "For each function_flow diagram, show a complete flow from input data to core processing, result output, validation/review, and business collaboration/closed-loop handling.",
                "For the architecture diagram, show a concise layered platform architecture based on the generated system architecture text.",
                "Use high-voltage transmission line UAV inspection domain language; do not use ECDIS, AIS, radar echo/navigation, route planning, NMEA, ship, or crew concepts unless they appear in the input requirements.",
                "Return JSON only. Do not wrap the response in Markdown fences.",
            ],
            "architecture_diagram_generation_logic": {
                "applies_to": "kind=architecture and title contains 总体架构/架构蓝图",
                "input_text": architecture_text,
                "prompt": architecture_prompt,
                "rules": [
                    "The architecture diagram must be generated from the architecture design explanation, not from scattered requirement titles.",
                    "Each layer may contain child modules, but child modules in different layers must not be connected directly.",
                    "Use Mermaid code format only in the mermaid field.",
                    "Keep the architecture diagram concise and readable for Word insertion.",
                ],
            },
            "project": {
                "name": blueprint.get("project_name") or requirements.get("project", {}).get("name") or "待确认项目",
                "run_id": self.run_id,
            },
            "diagram_plan": [
                {
                    "diagram_id": item.get("diagram_id"),
                    "title": item.get("title"),
                    "kind": item.get("kind"),
                    "purpose": item.get("purpose"),
                    "layout_hint": self.vertical_layout(item.get("layout_hint")),
                    "source_requirement_ids": item.get("source_requirement_ids", []),
                    "related_section_ids": item.get("related_section_ids", []),
                    "related_module_ids": item.get("related_module_ids", []),
                }
                for item in diagram_plan
            ],
            "diagram_prompts": [
                {
                    "diagram_id": item.get("diagram_id"),
                    "prompt": architecture_prompt
                    if self.is_overall_architecture_plan(item)
                    else "根据图表计划、相关章节和模块信息生成简洁可读的 Mermaid 图。Mermaid 代码必须使用 flowchart TB 或 flowchart TD，不要使用 Markdown 代码围栏。",
                    "context_text": architecture_text if self.is_overall_architecture_plan(item) else "",
                }
                for item in diagram_plan
            ],
            "requirements": [self.slim_requirement(requirements_by_id[req_id]) for req_id in required_ids if req_id in requirements_by_id],
            "architecture_layers": blueprint.get("architecture_layers", []),
            "modules": [
                self.slim_module(module)
                for module in modules
                if set(module.get("source_requirement_ids", [])) & set(required_ids)
                or any(diagram.get("diagram_id") in module.get("suggested_diagram_ids", []) for diagram in diagram_plan)
            ],
            "sections": [
                sections_by_id[section_id]
                for section_id in self.unique(
                    section_id
                    for diagram in diagram_plan
                    for section_id in diagram.get("related_section_ids", [])
                    if section_id in sections_by_id
                )
            ],
            "module_lookup": {
                module_id: self.slim_module(module)
                for module_id, module in modules_by_id.items()
                if module_id and any(module_id in diagram.get("related_module_ids", []) for diagram in diagram_plan)
            },
        }
        return request

    @staticmethod
    def architecture_diagram_prompt(architecture_text: str) -> str:
        return (
            f"{architecture_text}\n\n"
            "根据上面的描述，生成该系统的架构图。"
            "架构图中各层的子模块不允许跨层直连，采用 Mermaid 代码格式输出，"
            "架构图尽量简洁，并优先保证可读性。"
        ).strip()

    @staticmethod
    def is_overall_architecture_plan(plan: dict[str, Any]) -> bool:
        title = str(plan.get("title") or "")
        kind = str(plan.get("kind") or "")
        return kind == "architecture" and ("总体架构" in title or "架构蓝图" in title)

    @staticmethod
    def architecture_design_text(content_blocks: dict[str, Any]) -> str:
        if content_blocks.get("artifact") != "content-blocks":
            return ""
        candidates = []
        for block in content_blocks.get("blocks", []):
            title_text = " ".join(
                str(block.get(key, ""))
                for key in ("placeholder", "section_id")
            )
            if "总体架构设计" not in title_text and "总体架构" not in title_text:
                continue
            content = block.get("content")
            if isinstance(content, list):
                candidates.extend(str(item).strip() for item in content if str(item).strip())
            elif isinstance(content, str) and content.strip():
                candidates.append(content.strip())
        return "\n".join(candidates)

    def normalize_llm_response(self, response: dict[str, Any], diagram_plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(response, dict):
            raise ValueError("LLM API 必须返回 JSON object/dict。")
        raw_diagrams = response.get("diagrams")
        if not isinstance(raw_diagrams, list):
            raise ValueError("LLM API 返回值必须包含 diagrams 数组。")

        by_id = {str(item.get("diagram_id", "")): item for item in raw_diagrams if isinstance(item, dict)}
        diagrams = []
        for plan in diagram_plan:
            diagram_id = plan["diagram_id"]
            raw = by_id.get(diagram_id)
            if raw is None:
                raise ValueError(f"LLM API 未返回计划中的图表：{diagram_id}")

            mermaid = self.clean_mermaid(str(raw.get("mermaid", "")))
            description = str(raw.get("description", "")).strip() or str(plan.get("purpose", "")).strip()
            node_trace = self.normalize_node_trace(raw.get("node_trace", []), plan)
            review_notes = [str(note).strip() for note in raw.get("review_notes", []) if str(note).strip()]

            diagrams.append(
                {
                    "diagram_id": diagram_id,
                    "title": plan.get("title", diagram_id),
                    "kind": plan.get("kind", "other"),
                    "purpose": plan.get("purpose", ""),
                    "source_requirement_ids": self.unique_valid_req_ids(plan.get("source_requirement_ids", [])),
                    "related_section_ids": self.unique_valid_ids(plan.get("related_section_ids", []), SECTION_ID_RE),
                    "related_module_ids": self.unique_valid_ids(plan.get("related_module_ids", []), MODULE_ID_RE),
                    "layout_hint": self.vertical_layout(plan.get("layout_hint")),
                    "description": description,
                    "mermaid": mermaid,
                    "node_trace": node_trace,
                    "review_notes": review_notes,
                }
            )
        return diagrams

    def local_draft_response(
        self,
        requirements: dict[str, Any],
        blueprint: dict[str, Any],
        diagram_plan: list[dict[str, Any]],
        content_blocks: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build deterministic Mermaid drafts for offline end-to-end testing."""

        req_index = {item.get("requirement_id"): item for item in self.all_requirement_items(requirements)}
        module_index = {item.get("module_id"): item for item in blueprint.get("modules", []) if item.get("module_id")}
        section_index = {item.get("section_id"): item for item in blueprint.get("sections", []) if item.get("section_id")}
        architecture_text = self.architecture_design_text(content_blocks or {})

        diagrams = []
        for plan in diagram_plan:
            diagram_id = str(plan["diagram_id"])
            title = str(plan.get("title") or diagram_id)
            source_ids = self.unique_valid_req_ids(plan.get("source_requirement_ids", []))
            module_ids = self.unique_valid_ids(plan.get("related_module_ids", []), MODULE_ID_RE)
            section_ids = self.unique_valid_ids(plan.get("related_section_ids", []), SECTION_ID_RE)

            if self.is_overall_architecture_plan(plan) and architecture_text:
                nodes = self.local_architecture_nodes(source_ids, architecture_text)
                mermaid = self.render_local_architecture_mermaid(nodes)
                description = "根据总体架构设计说明生成的系统架构图，采用简洁分层表达，各层子模块不跨层直连。"
                review_notes = [
                    "本图由 --allow-local-draft 按架构设计说明生成；接入 LLM 后应使用 architecture_diagram_generation_logic.prompt 生成正式图。",
                    "已按本地规则控制各层子模块不跨层连线。",
                ]
            elif str(plan.get("kind")) == "function_flow" and len(source_ids) == 1:
                requirement = req_index.get(source_ids[0], {})
                nodes = self.local_function_flow_nodes(source_ids[0], requirement)
                mermaid = self.render_local_function_flow_mermaid(nodes)
                description = f"根据{source_ids[0]}功能要求生成的功能流程图，说明输入、处理、输出与异常记录。"
                review_notes = ["本图由 --allow-local-draft 按单条功能要求生成；接入 LLM 后应结合对应功能正文生成正式流程图。"]
            else:
                nodes = self.local_draft_nodes(plan, source_ids, module_ids, section_ids, req_index, module_index, section_index)
                mermaid = self.render_local_mermaid(title, nodes)
                description = str(plan.get("purpose") or f"{title}用于本地闭环验证。")
                review_notes = ["本图由 --allow-local-draft 生成，用于无 LLM 环境下的全流程验证，交付前建议复核图形表达。"]
            diagrams.append(
                {
                    "diagram_id": diagram_id,
                    "description": description,
                    "mermaid": mermaid,
                    "node_trace": [
                        {
                            "node_id": node["node_id"],
                            "label": node["label"],
                            "source_requirement_ids": node["source_requirement_ids"],
                        }
                        for node in nodes
                    ],
                    "review_notes": review_notes,
                }
            )
        return {"diagrams": diagrams}

    def local_function_flow_nodes(self, req_id: str, requirement: dict[str, Any]) -> list[dict[str, Any]]:
        title = str(requirement.get("title") or req_id)
        text = str(requirement.get("text") or "")
        source = [req_id]
        if "海图" in title or "海图" in text:
            labels = ["海图/用户操作输入", "数据解析与显示控制", "海图态势输出", "记录与异常提示"]
        elif "AIS" in title or "雷达" in title or "ARPA" in text or "目标" in text:
            labels = ["目标与本船数据输入", "时空对齐与融合处理", "目标态势叠加显示", "风险判断与记录"]
        elif "航线" in title or "航线" in text:
            labels = ["航点与安全参数输入", "航线编辑与安全检查", "航线监控结果输出", "航迹与告警记录"]
        elif "告警" in title or "预警" in title or "水深" in text or "等深线" in text:
            labels = ["船位/海图/目标输入", "安全规则判断", "声光告警与列表提示", "确认消警与事件记录"]
        elif "接口" in title or "NMEA" in text or "设备" in text:
            labels = ["外部设备/文件输入", "协议解析与接口适配", "标准数据对象输出", "通信状态与日志记录"]
        elif "交互" in title or "HMI" in title or "个性化" in title:
            labels = ["用户操作输入", "权限与操作校验", "界面反馈与配置保存", "误操作防护记录"]
        else:
            labels = ["输入条件", "功能处理", "结果输出", "异常与日志记录"]
        return [
            {"node_id": chr(ord("A") + index), "label": label, "source_requirement_ids": source}
            for index, label in enumerate(labels)
        ]

    @classmethod
    def render_local_function_flow_mermaid(cls, nodes: list[dict[str, Any]]) -> str:
        lines = ["flowchart TB"]
        for node in nodes:
            lines.append(f'    {node["node_id"]}["{cls.escape_mermaid_label(node["label"])}"]')
        for left, right in zip(nodes, nodes[1:]):
            lines.append(f"    {left['node_id']} --> {right['node_id']}")
        return "\n".join(lines)

    def local_architecture_nodes(self, source_ids: list[str], architecture_text: str = "") -> list[dict[str, Any]]:
        fallback = source_ids[:4] or ["T001"]
        layer_sources = [source_ids[index : index + 4] or fallback for index in range(0, 20, 4)]
        if any(keyword in architecture_text for keyword in ("无人机", "巡检", "杆塔", "点云", "LiDAR")):
            layer_labels = [
                "基础设施与数据资源层",
                "巡检数据接入层",
                "空间资产映射层",
                "智能分析服务层",
                "业务应用协同层",
            ]
            module_labels = [
                ["对象存储", "业务数据库"],
                ["巡检数据接入", "元数据解析"],
                ["杆塔匹配", "部件挂载"],
                ["缺陷识别", "点云测距"],
                ["人工复核", "工单流转"],
            ]
        else:
            layer_labels = [
                "数据接入与标准化处理层",
                "海图与目标态势融合层",
                "导航业务与安全决策层",
                "显示交互与控制层",
                "平台运行与质量保障层",
            ]
            module_labels = [
                ["海图与导航数据", "AIS/雷达/ARPA"],
                ["海图要素融合", "动态目标维护"],
                ["航线规划", "安全告警"],
                ["海图渲染", "图层控制"],
                ["跨平台适配", "日志审计"],
            ]
        nodes: list[dict[str, Any]] = []
        for layer_index, layer in enumerate(layer_labels, 1):
            layer_source = self.unique_valid_req_ids(layer_sources[layer_index - 1]) or fallback
            nodes.append({"node_id": f"L{layer_index}", "label": layer, "source_requirement_ids": layer_source})
            for module_index, module in enumerate(module_labels[layer_index - 1], 1):
                nodes.append(
                    {
                        "node_id": f"L{layer_index}M{module_index}",
                        "label": module,
                        "source_requirement_ids": layer_source[:2] or layer_source,
                    }
                )
        return nodes

    @classmethod
    def render_local_architecture_mermaid(cls, nodes: list[dict[str, Any]]) -> str:
        node_map = {node["node_id"]: node for node in nodes}
        lines = ["flowchart TB"]
        layer_ids = [f"L{index}" for index in range(1, 6)]
        for layer_id in layer_ids:
            layer = node_map[layer_id]
            lines.append(f'    {layer_id}["{cls.escape_mermaid_label(layer["label"])}"]')
        for left, right in zip(layer_ids, layer_ids[1:]):
            lines.append(f"    {left} --> {right}")
        for layer_index, layer_id in enumerate(layer_ids, 1):
            layer = node_map[layer_id]
            lines.append(f'    subgraph G{layer_index}["{cls.escape_mermaid_label(layer["label"])}"]')
            module_index = 1
            while f"L{layer_index}M{module_index}" in node_map:
                node_id = f"L{layer_index}M{module_index}"
                module = node_map[node_id]
                lines.append(f'        {node_id}["{cls.escape_mermaid_label(module["label"])}"]')
                module_index += 1
            lines.append("    end")
        return "\n".join(lines)

    def local_draft_nodes(
        self,
        plan: dict[str, Any],
        source_ids: list[str],
        module_ids: list[str],
        section_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        module_index: dict[str, dict[str, Any]],
        section_index: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        fallback_sources = source_ids[:1]
        nodes.append(
            {
                "node_id": "A",
                "label": self.clean_label(str(plan.get("title") or "图表主题")),
                "source_requirement_ids": fallback_sources,
            }
        )

        next_ord = ord("B")
        for module_id in module_ids:
            module = module_index.get(module_id)
            if not module:
                continue
            node_sources = [req_id for req_id in module.get("source_requirement_ids", []) if req_id in source_ids][:4]
            if not node_sources:
                node_sources = fallback_sources
            nodes.append(
                {
                    "node_id": chr(next_ord),
                    "label": self.clean_label(str(module.get("name") or module_id)),
                    "source_requirement_ids": node_sources,
                }
            )
            next_ord += 1
            if len(nodes) >= 10 or next_ord > ord("Z"):
                break

        if len(nodes) < 5:
            for section_id in section_ids:
                section = section_index.get(section_id)
                if not section:
                    continue
                node_sources = [req_id for req_id in section.get("source_requirement_ids", []) if req_id in source_ids][:4]
                if not node_sources:
                    node_sources = fallback_sources
                nodes.append(
                    {
                        "node_id": chr(next_ord),
                        "label": self.clean_label(str(section.get("title") or section_id)),
                        "source_requirement_ids": node_sources,
                    }
                )
                next_ord += 1
                if len(nodes) >= 10 or next_ord > ord("Z"):
                    break

        if len(nodes) < 5:
            for req_id in source_ids:
                requirement = req_index.get(req_id)
                if not requirement:
                    continue
                nodes.append(
                    {
                        "node_id": chr(next_ord),
                        "label": self.clean_label(str(requirement.get("title") or req_id)),
                        "source_requirement_ids": [req_id],
                    }
                )
                next_ord += 1
                if len(nodes) >= 10 or next_ord > ord("Z"):
                    break

        return nodes[:10]

    @classmethod
    def render_local_mermaid(cls, title: str, nodes: list[dict[str, Any]]) -> str:
        lines = ["flowchart TB"]
        for node in nodes:
            lines.append(f'    {node["node_id"]}["{cls.escape_mermaid_label(node["label"])}"]')
        if len(nodes) == 2:
            lines.append(f"    {nodes[0]['node_id']} --> {nodes[1]['node_id']}")
        elif len(nodes) > 2:
            root = nodes[0]["node_id"]
            for node in nodes[1:]:
                lines.append(f"    {root} --> {node['node_id']}")
        else:
            lines.append(f'    Z["{cls.escape_mermaid_label(title)}"]')
        return "\n".join(lines)

    @staticmethod
    def clean_label(value: str) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        value = re.sub(r"[\[\]{}()<>|`]", "", value)
        return MermaidAgent.truncate(value or "node", 28)

    @staticmethod
    def escape_mermaid_label(value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', "'")

    def build_diagram_specs(
        self,
        requirements: dict[str, Any],
        blueprint: dict[str, Any],
        diagram_plan_doc: dict[str, Any],
        diagrams: list[dict[str, Any]],
        output_diagram_dir: Path,
    ) -> dict[str, Any]:
        specs = {
            "schema_version": SCHEMA_VERSION,
            "artifact": "diagram-specs",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": {"agent": AGENT_NAME, "version": AGENT_VERSION},
            "inputs": [
                {
                    "artifact": "requirements",
                    "path": self.relative(self.records_dir / "requirements.json"),
                    "schema_version": str(requirements.get("schema_version", "unknown")),
                },
                {
                    "artifact": "design-blueprint",
                    "path": self.relative(self.records_dir / "design-blueprint.json"),
                    "schema_version": str(blueprint.get("schema_version", "unknown")),
                },
                {
                    "artifact": "diagram-plan",
                    "path": self.relative(self.records_dir / "diagram-plan.json"),
                    "schema_version": str(diagram_plan_doc.get("schema_version", "unknown")),
                },
            ],
            "diagrams": [],
        }
        for diagram in diagrams:
            mermaid_path = output_diagram_dir / f"{diagram['diagram_id']}.mmd"
            specs["diagrams"].append(
                {
                    "diagram_id": diagram["diagram_id"],
                    "title": diagram["title"],
                    "kind": diagram["kind"],
                    "mermaid_path": self.relative(mermaid_path),
                    "mermaid": diagram["mermaid"],
                    "source_requirement_ids": diagram["source_requirement_ids"],
                    "related_section_ids": diagram["related_section_ids"],
                    "related_module_ids": diagram["related_module_ids"],
                    "description": diagram["description"],
                    "node_trace": diagram["node_trace"],
                    "status": self.diagram_status(diagram),
                    "review_notes": diagram["review_notes"],
                }
            )
        return specs

    def validate_specs(self, specs: dict[str, Any]) -> None:
        if specs.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("diagram-specs schema_version must be 1.0.0")
        if specs.get("artifact") != "diagram-specs":
            raise ValueError("diagram-specs artifact must be diagram-specs")
        if not specs.get("diagrams"):
            raise ValueError("diagram-specs has no diagrams")

        architecture_count = sum(1 for diagram in specs["diagrams"] if diagram.get("kind") == "architecture" and "架构" in str(diagram.get("title", "")))
        function_flow_count = sum(1 for diagram in specs["diagrams"] if diagram.get("kind") == "function_flow")
        if architecture_count < 1:
            raise ValueError("diagram-specs 缺少系统总体架构图。")
        if function_flow_count < MIN_PRIMARY_FUNCTION_FLOW_DIAGRAMS:
            raise ValueError(f"diagram-specs 一级功能流程图不足：{function_flow_count}/{MIN_PRIMARY_FUNCTION_FLOW_DIAGRAMS}")

        for diagram in specs["diagrams"]:
            diagram_id = diagram.get("diagram_id", "")
            if not DIAGRAM_ID_RE.match(diagram_id):
                raise ValueError(f"Invalid diagram_id: {diagram_id}")
            if diagram.get("kind") not in ALLOWED_KINDS:
                raise ValueError(f"{diagram_id} has invalid kind")
            if not diagram.get("title"):
                raise ValueError(f"{diagram_id} missing title")
            if not diagram.get("description"):
                raise ValueError(f"{diagram_id} missing description")
            if not diagram.get("source_requirement_ids"):
                raise ValueError(f"{diagram_id} missing source_requirement_ids")
            if not self.is_allowed_mermaid(diagram.get("mermaid", "")):
                raise ValueError(f"{diagram_id} has invalid Mermaid source")
            pollution = self.domain_pollution_terms(diagram.get("mermaid", "") + " " + diagram.get("description", ""))
            if pollution:
                raise ValueError(f"{diagram_id} contains unrelated domain terms: {', '.join(pollution)}")

            allowed_sources = set(diagram["source_requirement_ids"])
            for trace in diagram.get("node_trace", []):
                node_id = str(trace.get("node_id", ""))
                if not NODE_ID_RE.match(node_id):
                    raise ValueError(f"{diagram_id} has invalid node_trace ID: {node_id}")
                source_ids = trace.get("source_requirement_ids", [])
                if not source_ids:
                    raise ValueError(f"{diagram_id} node {node_id} missing source_requirement_ids")
                unknown = [req_id for req_id in source_ids if req_id not in allowed_sources]
                if unknown:
                    raise ValueError(f"{diagram_id} node {node_id} references unknown IDs: {', '.join(unknown)}")

    @staticmethod
    def domain_pollution_terms(value: str) -> list[str]:
        forbidden = ("海图", "ECDIS", "AIS", "雷达回波", "ARPA", "NMEA", "航线", "船舶", "船员", "S-57", "S-63", "S-52")
        return [term for term in forbidden if term in str(value)]

    @staticmethod
    def render_descriptions(specs: dict[str, Any]) -> str:
        lines = [
            "# Mermaid 图表说明",
            "",
            f"- Run ID：`{specs['run_id']}`",
            f"- Generated at：`{specs['generated_at']}`",
            "",
            "| 图表 ID | 标题 | 类型 | Mermaid 文件 | 来源需求 ID | 说明 |",
            "|---|---|---|---|---|---|",
        ]
        for diagram in specs["diagrams"]:
            lines.append(
                "| {diagram_id} | {title} | {kind} | `{path}` | {sources} | {description} |".format(
                    diagram_id=diagram["diagram_id"],
                    title=MermaidAgent.escape_md(diagram["title"]),
                    kind=diagram["kind"],
                    path=diagram["mermaid_path"],
                    sources=", ".join(diagram["source_requirement_ids"]),
                    description=MermaidAgent.escape_md(diagram["description"]),
                )
            )
        lines.append("")
        lines.append("## 复核提示")
        lines.append("")
        for diagram in specs["diagrams"]:
            notes = diagram.get("review_notes") or ["无。"]
            lines.append(f"### {diagram['diagram_id']} {diagram['title']}")
            for note in notes:
                lines.append(f"- {note}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def collect_requirement_ids(requirements: dict[str, Any]) -> set[str]:
        return {item["requirement_id"] for item in MermaidAgent.all_requirement_items(requirements) if REQ_ID_RE.match(str(item.get("requirement_id", "")))}

    @staticmethod
    def all_requirement_items(requirements: dict[str, Any]) -> list[dict[str, Any]]:
        items = list(requirements.get("requirements", []))
        for delivery in requirements.get("delivery_items", []):
            delivery_id = delivery.get("delivery_id")
            if delivery_id:
                items.append(
                    {
                        "requirement_id": delivery_id,
                        "category": "delivery",
                        "title": delivery.get("name", delivery_id),
                        "text": delivery.get("name", ""),
                        "keywords": ["交付", "验收"],
                        "target_sections": ["交付方案", "项目实施计划"],
                        "status": delivery.get("status", "extracted"),
                        "risk_level": "normal",
                    }
                )
        return items

    @staticmethod
    def slim_requirement(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "requirement_id": item.get("requirement_id"),
            "category": item.get("category"),
            "title": item.get("title"),
            "text": MermaidAgent.truncate(str(item.get("text", "")), 420),
            "keywords": item.get("keywords", [])[:12],
            "target_sections": item.get("target_sections", []),
            "status": item.get("status", "extracted"),
            "risk_level": item.get("risk_level", "normal"),
        }

    @staticmethod
    def slim_module(module: dict[str, Any]) -> dict[str, Any]:
        return {
            "module_id": module.get("module_id"),
            "name": module.get("name"),
            "responsibility": MermaidAgent.truncate(str(module.get("responsibility", "")), 260),
            "layer_ids": module.get("layer_ids", []),
            "source_requirement_ids": module.get("source_requirement_ids", [])[:30],
            "suggested_diagram_ids": module.get("suggested_diagram_ids", []),
        }

    @staticmethod
    def normalize_node_trace(raw_trace: Any, plan: dict[str, Any]) -> list[dict[str, Any]]:
        allowed_sources = set(plan.get("source_requirement_ids", []))
        traces = []
        if isinstance(raw_trace, list):
            for raw in raw_trace:
                if not isinstance(raw, dict):
                    continue
                source_ids = [
                    str(req_id)
                    for req_id in raw.get("source_requirement_ids", [])
                    if str(req_id) in allowed_sources and REQ_ID_RE.match(str(req_id))
                ]
                if not source_ids:
                    continue
                node_id = str(raw.get("node_id", "")).strip()
                label = str(raw.get("label", "")).strip()
                if node_id and label:
                    traces.append({"node_id": node_id, "label": label, "source_requirement_ids": source_ids})
        if traces:
            return traces
        fallback_source = list(plan.get("source_requirement_ids", []))[:1]
        return [{"node_id": "A", "label": str(plan.get("title", "图表主题")), "source_requirement_ids": fallback_source}]

    @staticmethod
    def clean_mermaid(value: str) -> str:
        value = value.strip()
        if value.startswith("```"):
            value = re.sub(r"^```(?:mermaid)?\s*", "", value, flags=re.IGNORECASE)
            value = re.sub(r"\s*```$", "", value)
        lines = [line.rstrip() for line in value.splitlines() if line.strip()]
        return "\n".join(lines).strip()

    @staticmethod
    def is_allowed_mermaid(value: str) -> bool:
        return bool(FLOWCHART_RE.match(value))

    @staticmethod
    def diagram_status(diagram: dict[str, Any]) -> str:
        if diagram.get("review_notes"):
            return "review_required"
        return "generated"

    @staticmethod
    def vertical_layout(value: Any) -> str:
        layout = str(value or "").strip()
        if layout in {"flowchart TB", "flowchart TD"}:
            return layout
        return "flowchart TB"

    @staticmethod
    def unique(values: Any) -> list[Any]:
        result = []
        seen = set()
        for value in values:
            if value not in seen:
                seen.add(value)
                result.append(value)
        return result

    @staticmethod
    def unique_valid_req_ids(values: Any) -> list[str]:
        return MermaidAgent.unique(str(value) for value in values if REQ_ID_RE.match(str(value)))

    @staticmethod
    def unique_valid_ids(values: Any, pattern: re.Pattern[str]) -> list[str]:
        return MermaidAgent.unique(str(value) for value in values if pattern.match(str(value)))

    @staticmethod
    def truncate(value: str, limit: int) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if len(value) <= limit:
            return value
        return value[: limit - 3] + "..."

    @staticmethod
    def escape_md(value: str) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    def relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.workspace.resolve()).as_posix()
        except ValueError:
            return path.as_posix()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Mermaid Agent.")
    parser.add_argument("--workspace", default=".", help="Workspace root. Default: current directory.")
    parser.add_argument("--records-dir", default="output/records", help="Directory containing requirements/design/diagram plan records.")
    parser.add_argument("--output-dir", default="output/records", help="Directory for published record copies.")
    parser.add_argument(
        "--allow-local-draft",
        action="store_true",
        help="Generate deterministic local Mermaid drafts when call_llm_api is not configured.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    workspace = Path(args.workspace).resolve()
    records_dir = (workspace / args.records_dir).resolve()
    output_dir = (workspace / args.output_dir).resolve()
    agent = MermaidAgent(
        workspace=workspace,
        records_dir=records_dir,
        output_dir=output_dir,
        allow_local_draft=args.allow_local_draft,
    )
    paths = agent.run()
    print(f"Mermaid Agent completed: {agent.run_id}")
    print(f"Staging: {paths['staging_dir']}")
    print(f"Published: {paths['published_dir']}")


if __name__ == "__main__":
    main()
