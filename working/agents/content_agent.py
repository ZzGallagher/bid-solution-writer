#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Content Agent.

This stage consumes the requirement fact source and the design blueprint, then
emits Word-assembly-ready content blocks. Prose generation is intentionally
routed through ``llm_client.call_llm_api``. Fill the actual LLM API call only in
``working/agents/llm_client.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_client import LLMAPIUnavailable, call_llm_api


AGENT_NAME = "Content Agent"
AGENT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"

REQ_ID_RE = re.compile(r"^(T|P|Q|B|D)[0-9]{3}$")
SCORE_ID_RE = re.compile(r"^S[0-9]{3}$")
WRITING_ID_RE = re.compile(r"^WR[0-9]{3}$")

HIGH_RISK_KEYWORDS = {
    "personnel": ("人员", "负责人", "团队", "社保", "驻场", "讲师", "工程师"),
    "qualification": ("资质", "证书", "职称", "认证"),
    "performance_case": ("业绩", "案例", "合同", "验收报告", "销售"),
    "price": ("报价", "金额", "费用", "付款", "保证金", "价格"),
    "delivery_date": ("交付", "工期", "上线", "进场", "周期", "日期"),
    "service_commitment": ("质保", "保修", "响应时间", "响应时限", "上门", "7×24", "7*24", "24小时", "承诺"),
}

OVERCOMMITMENT_REPLACEMENTS = {
    "完全满足": "响应",
    "无偏离": "按要求响应",
    "零偏离": "按要求响应",
    "确保": "支撑",
    "保证": "保障",
    "承诺": "响应",
    "固定响应时间": "响应时限",
    "最短时间": "合理时间",
    "永久": "长期",
}

UNRESOLVED_PLACEHOLDER_PATTERNS = ("【GEN:", "【COPY:", "【REVIEW:", "{{", "}}", "TODO", "TBD")

TARGET_PLACEHOLDERS = {
    "【GEN:总体架构设计】",
    "【GEN:架构图说明】",
    "【GEN:功能设计总述】",
    "【GEN:功能设计章节】",
}

PROCESS_LEAK_PATTERNS = (
    "来源需求",
    "需求事实源",
    "结构化需求事实源",
    "payload",
    "占位符",
    "本章节按方案撰写要求",
    "本地草稿",
    "local-draft",
    "Review Gate",
    "Content Agent",
    "Design Agent",
)

DRONE_TARGET_HINTS = (
    "无人机",
    "巡检",
    "可见光",
    "红外",
    "LiDAR",
    "点云",
    "GIS",
    "杆塔",
    "输电",
    "缺陷",
    "工单",
)



class ContentAgent:
    def __init__(
        self,
        workspace: Path,
        records_dir: Path,
        output_dir: Path,
        model: str | None = None,
        allow_local_draft: bool = False,
    ) -> None:
        self.workspace = workspace
        self.records_dir = records_dir
        self.output_dir = output_dir
        self.model = model
        self.allow_local_draft = allow_local_draft
        now = datetime.now().astimezone()
        self.generated_at = now.isoformat(timespec="seconds")
        self.run_id = f"RUN-{now:%Y%m%d-%H%M%S}"
        self.review_index = 1
        self.confirm_index = 1
        self.review_items: list[dict[str, Any]] = []
        self.confirm_items: list[dict[str, Any]] = []
        self.notes: list[str] = []

    def run(self) -> dict[str, Path]:
        requirements = self.load_json(self.records_dir / "requirements.json")
        matrix = self.load_json(self.records_dir / "requirements-matrix.json")
        design = self.load_json(self.records_dir / "design-blueprint.json")
        section_plan = self.load_text(self.records_dir / "section-plan.md")

        self.run_id = str(requirements.get("run_id") or design.get("run_id") or self.run_id)
        req_index = self.requirement_index(requirements)
        score_index = self.scoring_index(requirements)
        writing_index = self.writing_index(requirements)
        module_index = {item["module_id"]: item for item in design.get("modules", []) if item.get("module_id")}
        diagram_plan = design.get("diagram_plan", [])

        blocks: list[dict[str, Any]] = []
        for index, section in enumerate(design.get("sections", []), 1):
            block = self.build_block(index, section, req_index, score_index, writing_index, module_index, diagram_plan, section_plan)
            blocks.append(block)

        self.import_requirement_lifecycle_items(requirements, blocks)

        artifact = {
            "schema_version": SCHEMA_VERSION,
            "artifact": "content-blocks",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": self.producer(),
            "inputs": self.build_inputs(requirements, matrix, design),
            "blocks": blocks,
            "review_items": self.review_items,
            "confirm_items": self.confirm_items,
        }
        self.validate_content_blocks(artifact, req_index, score_index, writing_index)

        staging_dir = self.workspace / "working" / "agent-system" / "staging" / "content" / self.run_id
        published_dir = self.workspace / "working" / "agent-system" / "published" / "content" / self.run_id
        for directory in (staging_dir, published_dir, self.output_dir):
            directory.mkdir(parents=True, exist_ok=True)

        outputs = {
            "content-blocks.json": json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            "content-preview.md": self.render_preview(artifact),
            "content-review-notes.md": self.render_review_notes(artifact),
        }
        for name, content in outputs.items():
            (staging_dir / name).write_text(content, encoding="utf-8")
        for name in outputs:
            shutil.copy2(staging_dir / name, published_dir / name)
            shutil.copy2(staging_dir / name, self.output_dir / name)

        return {"staging_dir": staging_dir, "published_dir": published_dir, "output_dir": self.output_dir}

    def build_block(
        self,
        index: int,
        section: dict[str, Any],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        writing_index: dict[str, dict[str, Any]],
        module_index: dict[str, dict[str, Any]],
        diagram_plan: list[dict[str, Any]],
        section_plan: str,
    ) -> dict[str, Any]:
        block_id = f"CB{index:03d}"
        req_ids = self.unique_valid_req_ids(section.get("source_requirement_ids", []))
        score_ids = self.unique_score_ids(section.get("related_scoring_item_ids", []))
        writing_ids = self.unique_writing_ids(section.get("writing_requirement_ids", []))
        status = self.status_for_section(section, req_ids, score_ids, writing_ids, req_index, score_index, writing_index)
        content_type = section.get("content_type", "generated_paragraphs")

        if content_type == "confirm_placeholder":
            block_type = "confirm_placeholder"
            content: Any = {"placeholder_text": section.get("placeholder", "【CONFIRM:待人工确认】")}
        elif content_type == "diagram_reference":
            diagram_id = self.diagram_for_section(section, diagram_plan)
            block_type = "diagram_reference"
            content = {"diagram_id": diagram_id, "caption": self.diagram_caption(diagram_id, diagram_plan, req_ids)}
        elif content_type == "generated_table":
            block_type = "table"
            content = self.generate_table_content(section, req_ids, score_ids, req_index, score_index)
        else:
            if content_type == "dynamic_sections":
                block_type = "rich_content"
            else:
                block_type = "review_text" if content_type == "review_text" else "paragraphs"
            content = self.generate_paragraphs(section, req_ids, score_ids, writing_ids, req_index, score_index, writing_index, module_index, section_plan, diagram_plan)

        risk_flags = self.risk_flags_for_content(content)
        if risk_flags and status == "generated":
            status = "review_required"
            self.notes.append(f"{block_id} 包含高风险关键词，已转入 review_required。")

        block: dict[str, Any] = {
            "block_id": block_id,
            "placeholder": section["placeholder"],
            "section_id": section["section_id"],
            "type": block_type,
            "content": content,
            "source_requirement_ids": req_ids,
            "scoring_item_ids": score_ids,
            "writing_requirement_ids": writing_ids,
            "status": status,
        }
        diagram_ids = self.diagram_ids_in_content(content)
        if block_type == "diagram_reference":
            diagram_ids = [content["diagram_id"]]
        if diagram_ids:
            block["diagram_ids"] = diagram_ids
        if risk_flags:
            block["risk_flags"] = risk_flags
        review_notes = self.review_notes_for_block(section, status, risk_flags)
        if review_notes:
            block["review_notes"] = review_notes

        if status == "confirm_required":
            self.add_confirm_item(block_id, f"{section['title']} 需人工确认后再装配。", req_ids + score_ids + writing_ids)
        elif status == "review_required":
            self.add_review_item(block_id, f"{section['title']} 需复核来源、表达边界、评分项响应和方案撰写要求扩写。", req_ids + score_ids + writing_ids)

        return block

    def generate_paragraphs(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        writing_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        writing_index: dict[str, dict[str, Any]],
        module_index: dict[str, dict[str, Any]],
        section_plan: str,
        diagram_plan: list[dict[str, Any]] | None = None,
    ) -> list[Any]:
        force_api = section.get("placeholder") in TARGET_PLACEHOLDERS
        if self.allow_local_draft and not force_api:
            paragraphs = self.local_draft(section, req_ids, score_ids, writing_ids, req_index, score_index, writing_index, module_index, diagram_plan or [])
        else:
            payload = self.build_llm_payload(section, req_ids, score_ids, writing_ids, req_index, score_index, writing_index, module_index, section_plan)
            try:
                response = call_llm_api(payload)
                paragraphs = self.normalize_llm_response(response)
            except LLMAPIUnavailable:
                raise
        if section.get("placeholder") not in TARGET_PLACEHOLDERS:
            paragraphs.extend(self.writing_requirement_paragraphs(section, writing_ids, writing_index))
        return [self.sanitize_content_item(item) for item in paragraphs if self.content_item_text(item).strip()]

    def build_llm_payload(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        writing_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        writing_index: dict[str, dict[str, Any]],
        module_index: dict[str, dict[str, Any]],
        section_plan: str,
    ) -> dict[str, Any]:
        modules = [module_index[module_id] for module_id in section.get("module_ids", []) if module_id in module_index]
        is_target = section.get("placeholder") in TARGET_PLACEHOLDERS
        rules = [
            "输出必须是可直接粘贴进投标技术方案的正文，不写文档生成任务说明，不解释你如何覆盖需求、评分项或追溯 ID。",
            "只依据 requirements、scoring_items、writing_requirements、modules 提炼业务背景、系统能力、技术路线和实施价值。",
            "writing_requirements 中的每条内容必须扩写后进入本章节正文，不能只摘要或遗漏。",
            "每段在语义上必须能追溯到 payload 中的 ID，但正文中不要出现来源 ID、需求矩阵、设计蓝图、payload、占位符等内部过程词。",
            "编写目的、项目背景、建设内容等总述章节要写成完整自然段，按背景挑战、系统建设内容、业务价值、本文档作用展开。",
            "架构设计和功能设计章节要从输入、处理、输出、控制、异常和验证角度描述系统本身，不要写成章节安排或资料清单。",
            "不新增人员、资质、业绩、报价、周期、服务承诺等事实。",
            "涉及 REVIEW 的内容写成初稿但保持可复核表达；涉及 CONFIRM 的事实不要补齐。",
            "避免使用“保证、完全满足、无偏离、确保、承诺、永久”等无来源强承诺措辞。",
            "输出 3 到 6 个正式、稳健、适合投标技术方案的中文段落。",
        ]
        if is_target:
            rules = [
                "本章节是最终投标方案正文，严禁出现来源需求、需求事实源、payload、占位符、本章节按方案撰写要求、本地草稿、Agent、Review Gate 等任何生成过程语言。",
                "系统架构章节必须达到样板深度，围绕无人机巡检平台的分层架构、数据链路、对象存储/结构化元数据、GIS空间计算、AI视觉分析中台、MLOps样本回流、LiDAR点云处理、缺陷管理与工单协同展开。",
                "功能设计章节必须达到样板深度，按功能模块展开业务定位、输入数据、处理机制、输出结果、异常/校验、业务价值，不要仅复述技术要求。",
                "必须使用高压输电线路无人机巡检领域表达，围绕可见光影像、红外热像、视频流、LiDAR点云、杆塔/部件台账、缺陷识别、人工复核、工单流转等内容组织。",
                "不要写人员、资质、业绩、报价、固定承诺等未在输入中提供的事实。",
                "输出自然正式的中文正文，段落可较长，风格向样板文件看齐。",
            ]
        return {
            "task": "generate_content_block",
            "agent": AGENT_NAME,
            "schema_version": SCHEMA_VERSION,
            "model": self.model,
            "section": {
                "section_id": section["section_id"],
                "title": section["title"],
                "content_type": section["content_type"],
                "status": section["status"],
                "placeholder": section["placeholder"],
            },
            "requirements": [self.source_brief(req_index[req_id]) for req_id in req_ids if req_id in req_index],
            "scoring_items": [self.score_brief(score_index[score_id]) for score_id in score_ids if score_id in score_index],
            "writing_requirements": [self.writing_brief(writing_index[writing_id]) for writing_id in writing_ids if writing_id in writing_index],
            "modules": [
                {
                    "module_id": module["module_id"],
                    "name": module["name"],
                    "responsibility": module["responsibility"],
                    "source_requirement_ids": module.get("source_requirement_ids", []),
                    "related_scoring_item_ids": module.get("related_scoring_item_ids", []),
                }
                for module in modules
            ],
            "section_plan_excerpt": section_plan[:4000],
            "rules": rules,
            "output_contract": {"content": ["段落1", "段落2"]},
        }

    def local_draft(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        writing_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        writing_index: dict[str, dict[str, Any]],
        module_index: dict[str, dict[str, Any]],
        diagram_plan: list[dict[str, Any]] | None = None,
    ) -> list[Any]:
        title = section["title"]
        context = self.local_context(section, req_ids, score_ids, writing_ids, req_index, score_index, writing_index, module_index, diagram_plan or [])

        if section.get("content_type") == "dynamic_sections":
            return self.local_dynamic_function_sections(context)
        if "总体架构" in title:
            return self.local_architecture_overview(context)
        if "架构图说明" in title:
            return self.local_architecture_diagram_text(context)
        if "设计原则" in title:
            return self.local_design_principles(context)
        if "部署" in title:
            return self.local_deployment_design(context)
        if "功能设计总述" in title:
            return self.local_function_overview(context)
        if "数据库" in title or "核心业务数据" in title:
            return self.local_data_design(context)
        if "性能" in title:
            return self.local_performance_design(context)
        if "可靠" in title:
            return self.local_reliability_design(context)
        if "维修" in title or "保障性" in title:
            return self.local_maintainability_design(context)
        if "测试" in title:
            return self.local_testability_design(context)
        if "安全" in title:
            return self.local_security_design(context)
        if "环境" in title:
            return self.local_environment_design(context)
        if "关键技术" in title:
            return self.local_key_technology_design(context)
        if "质量" in title or "风险" in title:
            return self.local_quality_risk_design(context)
        if "交付" in title or "验收" in title or "项目进度" in title:
            return self.local_delivery_design(context)
        if "培训" in title or "售后" in title or "应急" in title or "跟踪" in title:
            return self.local_service_design(context)
        if "编写目的" in title or "项目背景" in title:
            return self.local_project_purpose(context)
        if "建设内容" in title:
            return self.local_construction_scope(context)
        return self.local_generic_design(context)

    def local_context(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        writing_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        writing_index: dict[str, dict[str, Any]],
        module_index: dict[str, dict[str, Any]],
        diagram_plan: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        requirements = [req_index[req_id] for req_id in req_ids if req_id in req_index]
        scoring_items = [score_index[score_id] for score_id in score_ids if score_id in score_index]
        writing_items = [writing_index[writing_id] for writing_id in writing_ids if writing_id in writing_index]
        modules = [module_index[module_id] for module_id in section.get("module_ids", []) if module_id in module_index]
        all_text = " ".join(
            [section.get("title", ""), section.get("placeholder", "")]
            + [item.get("title", "") + " " + item.get("text", "") for item in requirements]
            + [item.get("title", "") + " " + item.get("text", "") for item in writing_items]
            + [item.get("name", "") + " " + item.get("responsibility", "") for item in modules]
        )
        source_label = ", ".join(req_ids[:8] + score_ids[:4] + writing_ids[:4])
        if len(req_ids) + len(score_ids) + len(writing_ids) > 16:
            source_label += "..."
        return {
            "section": section,
            "title": section["title"],
            "requirements": requirements,
            "scoring_items": scoring_items,
            "writing_requirements": writing_items,
            "modules": modules,
            "req_ids": req_ids,
            "score_ids": score_ids,
            "writing_ids": writing_ids,
            "req_titles": [item.get("title", item.get("requirement_id", "")) for item in requirements],
            "score_titles": [item.get("title", item.get("scoring_item_id", "")) for item in scoring_items],
            "writing_titles": [item.get("title", item.get("writing_requirement_id", "")) for item in writing_items],
            "module_names": [item.get("name", "") for item in modules],
            "source_label": source_label or "设计蓝图",
            "profile": self.detect_profile(all_text),
            "all_text": all_text,
            "diagram_by_req": self.diagram_by_requirement(diagram_plan or []),
        }

    @staticmethod
    def detect_profile(text: str) -> str:
        lower = text.lower()
        if "drone" in lower or "uav" in lower:
            return "drone_inspection"
        if "chart" in lower or "ais" in lower or "radar" in lower:
            return "electronic_chart"
        return "generic"

    @staticmethod
    def project_label(context: dict[str, Any]) -> str:
        return str(context.get("project_name") or "the project")

    def local_project_purpose(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_construction_scope(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_architecture_overview(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_architecture_diagram_text(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_design_principles(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_deployment_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_function_overview(self, context: dict[str, Any]) -> list[str]:
        title = context.get("title") or "功能设计"
        sources = self.join_titles(context.get("req_titles", []))
        return [
            f"{title}根据结构化需求事实源编写，围绕目标能力、处理流程、输出结果和质量控制展开，不新增无来源承诺。",
            f"本节实现内容应保持对{sources}的可追溯关系，并将需复核或确认的事项保留在人工检查流程中。",
        ]

    def local_dynamic_function_sections(self, context: dict[str, Any]) -> list[str]:
        items: list[str] = []
        for requirement in context.get("requirements", [])[:12]:
            items.extend(self.local_requirement_function_paragraphs(requirement, context))
        if items:
            return items
        for module in context.get("modules", [])[:12]:
            name = str(module.get("name") or "模块")
            req_ids = [req_id for req_id in module.get("source_requirement_ids", []) if req_id in context.get("req_ids", [])][:6]
            items.extend(self.local_module_paragraphs(name, ", ".join(req_ids) or "设计蓝图"))
        return items or self.local_function_overview(context)

    def local_requirement_function_paragraphs(self, requirement: dict[str, Any], context: dict[str, Any]) -> list[str]:
        req_id = str(requirement.get("requirement_id", ""))
        title = str(requirement.get("title") or requirement.get("text") or "需求")
        text = str(requirement.get("text") or title)
        diagram_id = self.diagram_by_requirement(context.get("diagram_plan", [])).get(req_id)
        diagram_text = f" 相关图表：{diagram_id}。" if diagram_id else ""
        return [
            f"针对{title}，方案应围绕来源需求 {req_id} 组织输入处理、业务逻辑、结果输出、异常处理和验证记录。{diagram_text}",
            f"本段草稿依据为：{self.shorten(text, 180)}",
        ]

    @staticmethod
    def trim_sentence_end(value: str) -> str:
        return str(value).strip().rstrip(".;:,")

    @staticmethod
    def function_subject(title: str, text: str) -> str:
        return str(title or text or "该功能")

    @staticmethod
    def function_scene(title: str, text: str, profile: str) -> str:
        return "适用业务场景"

    def function_mechanism(self, title: str, text: str, profile: str) -> str:
        return "结构化输入、处理、输出和验证控制"

    def function_output(self, title: str, text: str, profile: str) -> str:
        return "可追溯的处理结果和可复核记录"

    def function_validation(self, title: str, text: str, profile: str) -> str:
        return "配置复核、功能测试、集成测试和验收检查"

    @staticmethod
    def diagram_by_requirement(diagram_plan: list[dict[str, Any]]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for diagram in diagram_plan or []:
            diagram_id = str(diagram.get("diagram_id") or "")
            for req_id in diagram.get("source_requirement_ids", []) or []:
                mapping.setdefault(str(req_id), diagram_id)
        return mapping

    def local_module_paragraphs(self, name: str, source_text: str) -> list[str]:
        return [
            f"{name}规划为与{source_text}关联的可追溯模块，应明确输入、输出、职责边界和验证记录。",
            f"{name}的方案描述应避免无来源承诺，对不确定事实保留复核或确认流程。",
        ]

    def local_data_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_performance_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_reliability_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_maintainability_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_testability_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_security_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_environment_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_key_technology_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_quality_risk_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_delivery_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_service_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_generic_design(self, context: dict[str, Any]) -> list[str]:
        title = context.get("title") or "方案章节"
        sources = self.join_titles(context.get("req_titles", []))
        return [
            f"{title}根据结构化需求事实源生成，内容应保持对{sources}的可追溯关系。",
            "该本地草稿仅用于离线流程验证；生产正文应通过 llm_client.call_llm_api() 生成。",
        ]
    def generate_table_content(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        rows: list[list[str]] = []
        for req_id in req_ids:
            item = req_index.get(req_id)
            if not item:
                continue
            rows.append([req_id, item.get("title", req_id), self.shorten(item.get("text", item.get("name", "")), 120)])
        for score_id in score_ids:
            item = score_index.get(score_id)
            if not item:
                continue
            rows.append([score_id, f"评分项：{item.get('title', score_id)}", self.shorten(item.get("text", ""), 120)])
        return {"columns": ["来源ID", "响应对象", "正文响应要点"], "rows": rows or [["-", section["title"], "待补充来源后生成。"]]}

    def writing_requirement_paragraphs(
        self,
        section: dict[str, Any],
        writing_ids: list[str],
        writing_index: dict[str, dict[str, Any]],
    ) -> list[str]:
        paragraphs = []
        for writing_id in writing_ids:
            item = writing_index.get(writing_id)
            if not item:
                continue
            title = str(item.get("title") or section.get("title") or "方案撰写要求")
            text = str(item.get("text") or "")
            if not text:
                continue
            paragraphs.append(
                f"针对{title}，本章节按方案撰写要求进行扩写：{self.shorten(text, 260)}。"
                "方案内容应结合本节相关技术需求展开设计说明，明确响应思路、实现路径、约束边界和复核要求，避免引入未在权威 Markdown 中给出的人员、资质、价格、业绩等确定性事实。"
            )
            if item.get("mapping_confidence") == "low":
                self.notes.append(f"{writing_id} 为低置信度自动章节映射，已进入复核清单。")
        return paragraphs

    def diagram_for_section(self, section: dict[str, Any], diagram_plan: list[dict[str, Any]]) -> str:
        section_id = section["section_id"]
        req_ids = set(section.get("source_requirement_ids", []))
        for diagram in diagram_plan:
            if section_id in diagram.get("related_section_ids", []):
                return diagram["diagram_id"]
        for diagram in diagram_plan:
            if req_ids & set(diagram.get("source_requirement_ids", [])):
                return diagram["diagram_id"]
        raise RuntimeError(f"{section_id} 无法匹配设计蓝图中的 diagram_id。")

    def diagram_caption(self, diagram_id: str, diagram_plan: list[dict[str, Any]], req_ids: list[str]) -> str:
        diagram = next((item for item in diagram_plan if item.get("diagram_id") == diagram_id), {})
        title = diagram.get("title", diagram_id)
        return f"{title}，用于说明本节相关模块、数据流或流程关系。"

    def import_requirement_lifecycle_items(self, requirements: dict[str, Any], blocks: list[dict[str, Any]]) -> None:
        for item in requirements.get("confirm_candidates", []):
            status = item.get("status")
            source_ids = self.unique(item.get("source_ids", []))
            block_id = self.find_block_for_sources(blocks, source_ids)
            if status == "confirm_required":
                self.add_confirm_item(block_id, item.get("reason") or item.get("field") or "需人工确认。", source_ids)
            elif status == "review_required":
                self.add_review_item(block_id, item.get("reason") or item.get("field") or "需复核。", source_ids)
        for item in requirements.get("writing_requirements", []):
            writing_id = item.get("writing_requirement_id")
            if not writing_id:
                continue
            if item.get("mapping_confidence") == "low" or item.get("status") == "review_required":
                block_id = self.find_block_for_sources(blocks, [writing_id])
                self.add_review_item(block_id, "方案撰写要求为自动映射或需复核，需确认章节位置和扩写边界。", [writing_id])

    def find_block_for_sources(self, blocks: list[dict[str, Any]], source_ids: list[str]) -> str:
        if source_ids:
            source_set = set(source_ids)
            for block in blocks:
                block_sources = set(block.get("source_requirement_ids", [])) | set(block.get("scoring_item_ids", [])) | set(block.get("writing_requirement_ids", []))
                if source_set & block_sources:
                    return block["block_id"]
        confirm_block = next((block for block in blocks if block.get("status") == "confirm_required"), None)
        if confirm_block:
            return confirm_block["block_id"]
        review_block = next((block for block in blocks if block.get("status") == "review_required"), None)
        return review_block["block_id"] if review_block else blocks[0]["block_id"]

    def add_review_item(self, block_id: str, message: str, source_ids: list[str]) -> None:
        key = (block_id, tuple(source_ids), message)
        for item in self.review_items:
            if (item["block_id"], tuple(item.get("source_ids", [])), item["message"]) == key:
                return
        self.review_items.append(
            {
                "item_id": f"RV{self.review_index:03d}",
                "block_id": block_id,
                "message": message,
                "source_ids": self.unique_source_ids(source_ids),
                "status": "review_required",
            }
        )
        self.review_index += 1

    def add_confirm_item(self, block_id: str, message: str, source_ids: list[str]) -> None:
        key = (block_id, tuple(source_ids), message)
        for item in self.confirm_items:
            if (item["block_id"], tuple(item.get("source_ids", [])), item["message"]) == key:
                return
        self.confirm_items.append(
            {
                "item_id": f"CF{self.confirm_index:03d}",
                "block_id": block_id,
                "message": message,
                "source_ids": self.unique_source_ids(source_ids),
                "status": "confirm_required",
            }
        )
        self.confirm_index += 1

    def validate_content_blocks(
        self,
        artifact: dict[str, Any],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        writing_index: dict[str, dict[str, Any]],
    ) -> None:
        self.require_fields(
            artifact,
            ["schema_version", "artifact", "run_id", "generated_at", "producer", "inputs", "blocks", "review_items", "confirm_items"],
            "content-blocks.json",
        )
        if artifact["artifact"] != "content-blocks" or artifact["schema_version"] != SCHEMA_VERSION:
            raise RuntimeError("content-blocks.json artifact 或 schema_version 不符合契约。")
        block_ids = [block.get("block_id") for block in artifact["blocks"]]
        if len(block_ids) != len(set(block_ids)):
            raise RuntimeError("content-blocks.json block_id 存在重复。")
        known_req_ids = set(req_index)
        known_score_ids = set(score_index)
        known_writing_ids = set(writing_index)
        for block in artifact["blocks"]:
            self.require_fields(
                block,
                ["block_id", "placeholder", "section_id", "type", "content", "source_requirement_ids", "scoring_item_ids", "writing_requirement_ids", "status"],
                f"content-blocks.json#{block.get('block_id', '?')}",
            )
            if not re.match(r"^CB[0-9]{3}$", block["block_id"]):
                raise RuntimeError(f"Invalid block_id: {block['block_id']}")
            if not block["source_requirement_ids"] and not block["scoring_item_ids"] and not block["writing_requirement_ids"] and block["status"] != "confirm_required":
                raise RuntimeError(f"{block['block_id']} missing source IDs")
            invalid_req_ids = set(block["source_requirement_ids"]) - known_req_ids
            invalid_score_ids = set(block["scoring_item_ids"]) - known_score_ids
            invalid_writing_ids = set(block["writing_requirement_ids"]) - known_writing_ids
            if invalid_req_ids:
                raise RuntimeError(f"{block['block_id']} references unknown requirement IDs: {', '.join(sorted(invalid_req_ids))}")
            if invalid_score_ids:
                raise RuntimeError(f"{block['block_id']} references unknown scoring IDs: {', '.join(sorted(invalid_score_ids))}")
            if invalid_writing_ids:
                raise RuntimeError(f"{block['block_id']} references unknown writing requirement IDs: {', '.join(sorted(invalid_writing_ids))}")
            text = self.content_text(block["content"])
            unresolved = [pattern for pattern in UNRESOLVED_PLACEHOLDER_PATTERNS if pattern in text]
            if unresolved:
                raise RuntimeError(f"{block['block_id']} has unresolved placeholders: {', '.join(unresolved)}")
            if block.get("placeholder") in TARGET_PLACEHOLDERS:
                process_leaks = [pattern for pattern in PROCESS_LEAK_PATTERNS if pattern in text]
                if process_leaks:
                    raise RuntimeError(f"{block['block_id']} contains process language: {', '.join(process_leaks)}")
                if block.get("placeholder") in {"【GEN:总体架构设计】", "【GEN:功能设计总述】", "【GEN:功能设计章节】"}:
                    if not any(hint in text for hint in DRONE_TARGET_HINTS):
                        raise RuntimeError(f"{block['block_id']} does not look like high-voltage UAV inspection content.")

        valid_block_ids = set(block_ids)
        for field in ("review_items", "confirm_items"):
            prefix = "RV" if field == "review_items" else "CF"
            item_ids = [item.get("item_id") for item in artifact[field]]
            if len(item_ids) != len(set(item_ids)):
                raise RuntimeError(f"{field} item_id duplicated")
            for item in artifact[field]:
                self.require_fields(item, ["item_id", "block_id", "message", "source_ids", "status"], field)
                if not re.match(rf"^{prefix}[0-9]{{3}}$", item["item_id"]):
                    raise RuntimeError(f"Invalid {field} item_id: {item['item_id']}")
                if item["block_id"] not in valid_block_ids:
                    raise RuntimeError(f"{field} references unknown block_id: {item['block_id']}")

    def load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Missing Content Agent input: {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - convert to stage failure
            raise RuntimeError(f"Unable to parse JSON input: {path}") from exc

    @staticmethod
    def load_text(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def build_inputs(self, requirements: dict[str, Any], matrix: dict[str, Any], design: dict[str, Any]) -> list[dict[str, str]]:
        inputs = [
            {"artifact": "requirements", "path": "output/records/requirements.json", "schema_version": str(requirements.get("schema_version", "unknown"))},
            {"artifact": "requirements-matrix", "path": "output/records/requirements-matrix.json", "schema_version": str(matrix.get("schema_version", "unknown"))},
            {"artifact": "design-blueprint", "path": "output/records/design-blueprint.json", "schema_version": str(design.get("schema_version", "unknown"))},
        ]
        if (self.records_dir / "section-plan.md").exists():
            inputs.append({"artifact": "section-plan", "path": "output/records/section-plan.md", "schema_version": "markdown"})
        rules = self.workspace / "docs" / "contracts" / "validation-rules.md"
        if rules.exists():
            inputs.append({"artifact": "generation-rules", "path": self.relative(rules), "schema_version": "markdown"})
        return inputs

    def producer(self) -> dict[str, str]:
        producer = {"agent": AGENT_NAME, "version": AGENT_VERSION}
        if self.model:
            producer["model"] = self.model
        elif self.allow_local_draft:
            producer["model"] = "local-draft"
        return producer

    @staticmethod
    def requirement_index(requirements: dict[str, Any]) -> dict[str, dict[str, Any]]:
        index = {item["requirement_id"]: item for item in requirements.get("requirements", []) if item.get("requirement_id")}
        for item in requirements.get("delivery_items", []):
            delivery_id = item.get("delivery_id")
            if not delivery_id:
                continue
            index[delivery_id] = {
                "requirement_id": delivery_id,
                "category": "delivery",
                "title": item.get("name", delivery_id),
                "text": ", ".join(str(part) for part in (item.get("name"), item.get("quantity"), item.get("medium")) if part),
                "source": item.get("source", {}),
                "status": item.get("status", "extracted"),
                "risk_level": "normal",
            }
        return index

    @staticmethod
    def scoring_index(requirements: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {item["scoring_item_id"]: item for item in requirements.get("scoring_items", []) if item.get("scoring_item_id")}

    @staticmethod
    def writing_index(requirements: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {
            item["writing_requirement_id"]: item
            for item in requirements.get("writing_requirements", [])
            if item.get("writing_requirement_id")
        }

    def status_for_section(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        writing_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        writing_index: dict[str, dict[str, Any]],
    ) -> str:
        if section.get("content_type") == "confirm_placeholder" or section.get("status") == "confirm_required":
            return "confirm_required"
        statuses = [req_index.get(req_id, {}).get("status") for req_id in req_ids]
        statuses.extend(score_index.get(score_id, {}).get("status") for score_id in score_ids)
        statuses.extend(writing_index.get(writing_id, {}).get("status") for writing_id in writing_ids)
        if section.get("content_type") == "review_text" or section.get("status") == "review_required" or "review_required" in statuses:
            return "review_required"
        return "generated"

    @staticmethod
    def source_brief(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("requirement_id"),
            "category": item.get("category"),
            "title": item.get("title"),
            "text": item.get("text"),
            "status": item.get("status"),
            "risk_level": item.get("risk_level"),
            "source_quote": item.get("source", {}).get("quote"),
        }

    @staticmethod
    def score_brief(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("scoring_item_id"),
            "title": item.get("title"),
            "text": item.get("text"),
            "score": item.get("score"),
            "response_section": item.get("response_section"),
            "status": item.get("status"),
        }

    @staticmethod
    def writing_brief(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("writing_requirement_id"),
            "title": item.get("title"),
            "text": item.get("text"),
            "target_sections": item.get("target_sections", []),
            "mandatory_expansion": item.get("mandatory_expansion"),
            "mapping_confidence": item.get("mapping_confidence"),
            "status": item.get("status"),
        }

    @staticmethod
    def normalize_llm_response(response: dict[str, Any] | list[str] | str) -> list[str]:
        if isinstance(response, dict):
            content = response.get("content")
            if isinstance(content, list):
                return [str(item).strip() for item in content if str(item).strip()]
            if isinstance(content, str):
                return [item.strip() for item in re.split(r"\n\s*\n|\n", content) if item.strip()]
        if isinstance(response, list):
            return [str(item).strip() for item in response if str(item).strip()]
        if isinstance(response, str):
            return [item.strip() for item in re.split(r"\n\s*\n|\n", response) if item.strip()]
        raise RuntimeError("LLM API 返回格式不符合 Content Agent 契约。")

    @staticmethod
    def sanitize_claims(value: str) -> str:
        text = re.sub(r"\s+", " ", value).strip()
        for source, replacement in OVERCOMMITMENT_REPLACEMENTS.items():
            text = text.replace(source, replacement)
        return text

    def sanitize_content_item(self, item: Any) -> Any:
        if isinstance(item, str):
            return self.sanitize_claims(item)
        if isinstance(item, dict):
            sanitized = dict(item)
            if "text" in sanitized:
                sanitized["text"] = self.sanitize_claims(str(sanitized["text"]))
            if "caption" in sanitized:
                sanitized["caption"] = self.sanitize_claims(str(sanitized["caption"]))
            return sanitized
        return item

    @staticmethod
    def content_item_text(item: Any) -> str:
        if isinstance(item, dict):
            return " ".join(str(value) for value in item.values())
        return str(item or "")

    @staticmethod
    def diagram_ids_in_content(content: Any) -> list[str]:
        ids: list[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "diagram" and item.get("diagram_id"):
                    ids.append(str(item["diagram_id"]))
        elif isinstance(content, dict) and content.get("diagram_id"):
            ids.append(str(content["diagram_id"]))
        return list(dict.fromkeys(ids))

    @staticmethod
    def risk_flags_for_content(content: Any) -> list[str]:
        text = ContentAgent.content_text(content)
        flags = [flag for flag, keywords in HIGH_RISK_KEYWORDS.items() if any(keyword in text for keyword in keywords)]
        return list(dict.fromkeys(flags))

    @staticmethod
    def review_notes_for_block(section: dict[str, Any], status: str, risk_flags: list[str]) -> list[str]:
        notes = []
        if status == "review_required":
            notes.append("Review Gate should verify sources, boundaries, and scoring responses.")
        if status == "confirm_required":
            notes.append("CONFIRM placeholder remains for manual confirmation before Word assembly.")
        if risk_flags:
            notes.append("Detected high-risk facts: " + ", ".join(risk_flags))
        if section.get("content_type") == "review_text":
            notes.append("This block is review_text and should be checked manually.")
        return notes

    def render_preview(self, artifact: dict[str, Any]) -> str:
        lines = [
            "# Content Preview",
            "",
            f"- Run ID: `{artifact['run_id']}`",
            f"- Generated At: `{artifact['generated_at']}`",
            "",
        ]
        for block in artifact["blocks"]:
            source_ids = block["source_requirement_ids"] + block["scoring_item_ids"] + block.get("writing_requirement_ids", [])
            lines.extend([f"## {block['block_id']} {block['placeholder']}", "", f"- Status: `{block['status']}`", f"- Source IDs: {', '.join(source_ids) or '-'}", ""])
            content = block["content"]
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "diagram":
                        lines.append(f"![{item.get('caption', item.get('diagram_id', 'diagram'))}]({item.get('diagram_id')})")
                    elif isinstance(item, dict):
                        lines.append(str(item.get("text") or item.get("caption") or item))
                    else:
                        lines.append(str(item))
            elif isinstance(content, dict) and block["type"] == "table":
                lines.append("| " + " | ".join(content["columns"]) + " |")
                lines.append("|" + "|".join("---" for _ in content["columns"]) + "|")
                for row in content["rows"]:
                    lines.append("| " + " | ".join(self.escape_md(cell) for cell in row) + " |")
            else:
                lines.append(self.content_text(content))
            lines.append("")
        return "\n".join(lines)

    def render_review_notes(self, artifact: dict[str, Any]) -> str:
        lines = [
            "# Content Review Notes",
            "",
            f"- Run ID: `{artifact['run_id']}`",
            f"- Generated At: `{artifact['generated_at']}`",
            "",
            "## Review Items",
            "",
        ]
        lines.extend(self.render_lifecycle_table(artifact["review_items"]))
        lines.extend(["", "## Confirm Items", ""])
        lines.extend(self.render_lifecycle_table(artifact["confirm_items"]))
        if self.notes:
            lines.extend(["", "## Agent Notes", ""])
            lines.extend(f"- {note}" for note in self.notes)
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def render_lifecycle_table(items: list[dict[str, Any]]) -> list[str]:
        if not items:
            return ["鏈鏈櫥璁扮浉鍏充簨椤广€?"]
        lines = ["| ID | Block | Status | Source IDs | Message |", "|---|---|---|---|---|"]
        for item in items:
            lines.append(
                f"| {item['item_id']} | {item['block_id']} | {item['status']} | {', '.join(item.get('source_ids', [])) or '-'} | {ContentAgent.escape_md(item['message'])} |"
            )
        return lines

    @staticmethod
    def content_text(content: Any) -> str:
        if isinstance(content, list):
            return " ".join(str(item) for item in content)
        if isinstance(content, dict):
            parts: list[str] = []
            for value in content.values():
                if isinstance(value, list):
                    parts.extend(str(item) for item in value)
                elif isinstance(value, dict):
                    parts.extend(str(item) for item in value.values())
                else:
                    parts.append(str(value))
            return " ".join(parts)
        return str(content or "")

    @staticmethod
    def require_fields(obj: dict[str, Any], fields: list[str], label: str) -> None:
        missing = [field for field in fields if field not in obj]
        if missing:
            raise RuntimeError(f"{label} missing fields: {', '.join(missing)}")

    @staticmethod
    def unique(values: Any) -> list[str]:
        return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))

    def unique_valid_req_ids(self, values: Any) -> list[str]:
        return [value for value in self.unique(values) if REQ_ID_RE.match(value)]

    def unique_score_ids(self, values: Any) -> list[str]:
        return [value for value in self.unique(values) if SCORE_ID_RE.match(value)]

    def unique_writing_ids(self, values: Any) -> list[str]:
        return [value for value in self.unique(values) if WRITING_ID_RE.match(value)]

    def unique_source_ids(self, values: Any) -> list[str]:
        return [value for value in self.unique(values) if REQ_ID_RE.match(value) or SCORE_ID_RE.match(value) or WRITING_ID_RE.match(value)]

    @staticmethod
    def join_titles(values: list[str]) -> str:
        values = [value for value in dict.fromkeys(values) if value]
        if not values:
            return "pending"
        text = ", ".join(values)
        return text if len(text) <= 80 else text[:79] + "..."

    @staticmethod
    def shorten(value: str, max_length: int) -> str:
        value = re.sub(r"\s+", " ", str(value)).strip()
        if len(value) <= max_length:
            return value
        return value[: max_length - 1] + "..."

    @staticmethod
    def escape_md(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace.resolve())).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Content Agent.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root.")
    parser.add_argument("--records-dir", type=Path, default=Path("output/records"), help="Directory containing published upstream artifacts.")
    parser.add_argument("--output-dir", type=Path, default=Path("output/records"), help="Published record output directory.")
    parser.add_argument("--model", default=None, help="Model label recorded in producer metadata.")
    parser.add_argument(
        "--allow-local-draft",
        action="store_true",
        help="Use deterministic local draft text only when call_llm_api is not filled. Intended for schema verification.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    workspace = args.workspace.resolve()
    records_dir = args.records_dir if args.records_dir.is_absolute() else workspace / args.records_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else workspace / args.output_dir

    agent = ContentAgent(
        workspace=workspace,
        records_dir=records_dir,
        output_dir=output_dir,
        model=args.model,
        allow_local_draft=args.allow_local_draft,
    )
    paths = agent.run()
    print(f"Content Agent completed: {agent.run_id}")
    print(f"staging: {paths['staging_dir']}")
    print(f"published: {paths['published_dir']}")
    print(f"output: {paths['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
