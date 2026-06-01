#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Design Agent.

This stage consumes the requirement fact source and turns it into a design
blueprint for downstream Content and Mermaid agents. It plans architecture
layers, modules, response sections, diagrams, and source-ID coverage without
writing final prose or Mermaid source.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from llm_client import call_llm_api


AGENT_NAME = "Design Agent"
AGENT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"
WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

REQ_ID_RE = re.compile(r"^(T|P|Q|B|D)[0-9]{3}$")
SCORE_ID_RE = re.compile(r"^S[0-9]{3}$")
WRITING_ID_RE = re.compile(r"^WR[0-9]{3}$")

TARGET_PLACEHOLDERS = {
    "【GEN:总体架构设计】",
    "【GEN:架构图说明】",
    "【GEN:功能设计总述】",
    "【GEN:功能设计章节】",
}

FORBIDDEN_DOMAIN_TERMS = ("海图", "AIS", "雷达回波", "ARPA", "ECDIS", "S-57", "S-63", "S-52", "NMEA", "航线", "船舶", "船员")

SECTION_TARGETS = [
    {
        "title": "编写目的",
        "placeholder": "【GEN:编写目的】",
        "content_type": "generated_paragraphs",
        "match_sections": {"编写目的", "需求分析", "技术方案"},
        "match_categories": {"technical_function", "technical_quality", "business", "delivery"},
        "match_scores": {"技术方案"},
    },
    {
        "title": "总体架构设计",
        "placeholder": "【GEN:总体架构设计】",
        "content_type": "generated_paragraphs",
        "match_sections": {"总体架构设计"},
        "match_categories": set(),
        "match_scores": {"总体架构设计"},
    },
    {
        "title": "总体架构图",
        "placeholder": "【GEN:总体架构图】",
        "content_type": "diagram_reference",
        "match_sections": {"总体架构设计"},
        "match_categories": set(),
        "match_scores": {"总体架构设计"},
    },
    {
        "title": "架构图说明",
        "placeholder": "【GEN:架构图说明】",
        "content_type": "generated_paragraphs",
        "match_sections": {"总体架构设计"},
        "match_categories": set(),
        "match_scores": {"总体架构设计"},
    },
    {
        "title": "设计原则",
        "placeholder": "【GEN:设计原则】",
        "content_type": "generated_paragraphs",
        "match_sections": {"技术标准响应", "质量保证", "质量与安全设计"},
        "match_categories": {"technical_quality"},
        "match_scores": {"技术方案"},
    },
    {
        "title": "部署架构设计",
        "placeholder": "【GEN:部署架构设计】",
        "content_type": "generated_paragraphs",
        "match_sections": {"部署方案"},
        "match_categories": set(),
        "match_scores": set(),
    },
    {
        "title": "功能设计总述",
        "placeholder": "【GEN:功能设计总述】",
        "content_type": "generated_paragraphs",
        "match_sections": {"功能设计"},
        "match_categories": {"technical_function"},
        "match_scores": set(),
    },
    {
        "title": "功能设计章节",
        "placeholder": "【GEN:功能设计章节】",
        "content_type": "dynamic_sections",
        "match_sections": {"功能设计", "接口设计", "安全设计"},
        "match_categories": {"technical_function"},
        "match_scores": set(),
    },
    {
        "title": "性能设计章节",
        "placeholder": "【GEN:性能设计章节】",
        "content_type": "generated_table",
        "match_sections": {"性能设计", "技术指标响应"},
        "match_categories": {"technical_performance"},
        "match_scores": {"技术指标响应"},
    },
    {
        "title": "数据库架构设计",
        "placeholder": "【GEN:数据库架构设计】",
        "content_type": "generated_paragraphs",
        "match_sections": {"接口设计"},
        "match_categories": set(),
        "match_scores": set(),
    },
    {
        "title": "核心业务数据设计",
        "placeholder": "【GEN:核心业务数据设计】",
        "content_type": "generated_paragraphs",
        "match_sections": {"接口设计", "功能设计"},
        "match_categories": set(),
        "match_scores": set(),
        "keyword_any": {"数据", "海图", "AIS", "雷达", "接口", "数据库"},
    },
    {
        "title": "通用质量特性设计总述",
        "placeholder": "【GEN:通用质量特性设计总述】",
        "content_type": "generated_paragraphs",
        "match_sections": {"质量与安全设计"},
        "match_categories": {"technical_quality"},
        "match_scores": set(),
    },
    {
        "title": "可靠性设计",
        "placeholder": "【GEN:可靠性设计】",
        "content_type": "generated_paragraphs",
        "match_sections": {"质量保证与测试方案", "质量与安全设计"},
        "match_categories": {"technical_quality", "technical_performance"},
        "match_scores": set(),
        "keyword_any": {"可靠", "稳定", "MTTR", "响应率", "初始化"},
    },
    {
        "title": "维修性与保障性设计",
        "placeholder": "【GEN:维修性设计】",
        "content_type": "generated_paragraphs",
        "match_sections": {"质量保证与测试方案", "培训与售后服务方案", "交付方案"},
        "match_categories": {"business", "delivery"},
        "match_scores": {"培训与售后服务方案"},
    },
    {
        "title": "测试性设计",
        "placeholder": "【GEN:测试性设计】",
        "content_type": "generated_paragraphs",
        "match_sections": {"质量保证与测试方案"},
        "match_categories": {"technical_quality"},
        "match_scores": set(),
        "keyword_any": {"测试", "验收", "检测", "评价"},
    },
    {
        "title": "安全性设计",
        "placeholder": "【GEN:安全性设计】",
        "content_type": "generated_paragraphs",
        "match_sections": {"安全设计", "安全与保密方案", "质量与安全设计"},
        "match_categories": set(),
        "match_scores": set(),
    },
    {
        "title": "环境适应性设计",
        "placeholder": "【GEN:环境适应性设计】",
        "content_type": "generated_paragraphs",
        "match_sections": {"部署方案", "质量与安全设计"},
        "match_categories": set(),
        "match_scores": set(),
        "keyword_any": {"环境", "国产化", "软硬件", "运行"},
    },
    {
        "title": "关键技术",
        "placeholder": "【GEN:关键技术】",
        "content_type": "generated_paragraphs",
        "match_sections": {"技术方案", "功能设计", "总体架构设计"},
        "match_categories": {"technical_function"},
        "match_scores": {"技术方案"},
    },
    {
        "title": "质量控制与风险管理",
        "placeholder": "【GEN:风险评估与控制】",
        "content_type": "review_text",
        "match_sections": {"质量保证与测试方案", "质量与安全设计", "商务响应"},
        "match_categories": {"business", "technical_quality"},
        "match_scores": {"项目管理和实施"},
    },
    {
        "title": "项目实施与交付验收",
        "placeholder": "【REVIEW:成果交付及验收】",
        "content_type": "review_text",
        "match_sections": {"项目实施计划", "交付方案", "商务响应"},
        "match_categories": {"business", "delivery"},
        "match_scores": {"项目管理和实施"},
    },
    {
        "title": "人员与资质证明",
        "placeholder": "【CONFIRM:项目团队人员说明】",
        "content_type": "confirm_placeholder",
        "match_sections": set(),
        "match_categories": set(),
        "match_scores": {"人员与资质证明"},
    },
    {
        "title": "培训与售后服务方案",
        "placeholder": "【GEN:培训方案】",
        "content_type": "review_text",
        "match_sections": {"培训与售后服务方案", "交付方案"},
        "match_categories": {"business"},
        "match_scores": {"培训与售后服务方案"},
    },
]

DIAGRAM_TEMPLATES = [
    {
        "title": "总体架构蓝图",
        "kind": "architecture",
        "purpose": "展示用户交互、业务功能、数据接口、平台运行和质量安全保障之间的分层关系。",
        "layout_hint": "flowchart TB",
        "selectors": {"sections": {"总体架构设计"}, "categories": {"technical_function", "technical_quality"}},
        "limit": 14,
    },
    {
        "title": "功能模块划分图",
        "kind": "architecture",
        "purpose": "展示海图显示、态势融合、航线规划、告警监控、个性化配置和数据维护等功能模块边界。",
        "layout_hint": "flowchart TB",
        "selectors": {"sections": {"功能设计"}, "categories": {"technical_function"}},
        "limit": 40,
    },
    {
        "title": "核心业务流程图",
        "kind": "business_flow",
        "purpose": "说明电子海图加载、目标信息融合、航线规划、航行监控和告警处置的主业务路径。",
        "layout_hint": "flowchart TB",
        "selectors": {"keywords": {"海图", "AIS", "雷达", "航线", "告警", "监控"}},
        "limit": 32,
    },
    {
        "title": "数据流与接口关系图",
        "kind": "data_flow",
        "purpose": "说明外部导航设备、文件系统、数据库读写、HMI 控制和业务模块之间的数据流向。",
        "layout_hint": "flowchart TB",
        "selectors": {"sections": {"接口设计"}, "keywords": {"接口", "数据", "数据库", "NMEA", "传感器", "文件"}},
        "limit": 28,
    },
    {
        "title": "部署与运行支撑图",
        "kind": "deployment",
        "purpose": "说明国产化软硬件环境、跨平台运行、多设备协同和性能保障的部署关系。",
        "layout_hint": "flowchart TB",
        "selectors": {"sections": {"部署方案", "性能设计", "技术指标响应"}, "keywords": {"部署", "国产化", "跨平台", "性能", "初始化", "响应"}},
        "limit": 24,
    },
    {
        "title": "安全与质量保障流程图",
        "kind": "security",
        "purpose": "说明权限、保密、质量监督、测试验收和风险处置的闭环控制。",
        "layout_hint": "flowchart TB",
        "selectors": {"sections": {"安全设计", "质量与安全设计", "质量保证与测试方案", "安全与保密方案"}},
        "limit": 36,
    },
    {
        "title": "项目实施与交付流程图",
        "kind": "business_flow",
        "purpose": "说明实施计划、交付物、验收、培训和售后服务的过程衔接。",
        "layout_hint": "flowchart TB",
        "selectors": {"sections": {"项目实施计划", "交付方案", "培训与售后服务方案", "商务响应"}, "categories": {"business", "delivery"}},
        "limit": 24,
    },
]


class DesignAgent:
    def __init__(self, workspace: Path, records_dir: Path, template_dir: Path, output_dir: Path) -> None:
        self.workspace = workspace
        self.records_dir = records_dir
        self.template_dir = template_dir
        self.output_dir = output_dir
        now = datetime.now().astimezone()
        self.generated_at = now.isoformat(timespec="seconds")
        self.run_id = f"RUN-{now:%Y%m%d-%H%M%S}"
        self.warnings: list[str] = []
        self.placeholders: list[str] = []

    def run(self) -> dict[str, Path]:
        requirements = self.load_json(self.records_dir / "requirements.json")
        matrix = self.load_json(self.records_dir / "requirements-matrix.json")
        self.run_id = str(requirements.get("run_id") or self.run_id)
        self.placeholders = self.extract_placeholders()

        req_items = requirements.get("requirements", [])
        writing_items = requirements.get("writing_requirements", [])
        scoring_items = requirements.get("scoring_items", [])
        delivery_items = self.delivery_as_requirements(requirements.get("delivery_items", []))

        api_blueprint = self.generate_target_blueprint_with_api(requirements, req_items, scoring_items, writing_items)
        architecture_layers = api_blueprint["architecture_layers"]
        modules = api_blueprint["modules"]
        sections = self.build_sections(req_items + delivery_items, scoring_items, writing_items)
        sections = self.merge_target_sections(sections, api_blueprint["sections"], req_items, scoring_items, writing_items)
        self.attach_api_modules_to_sections(sections, modules)
        diagram_plan = api_blueprint["diagram_plan"]
        self.retarget_diagram_sections(diagram_plan, sections)
        self.attach_diagrams_to_modules(modules, diagram_plan)
        coverage_map = self.build_coverage_map(req_items, scoring_items, delivery_items, writing_items, modules, sections, diagram_plan)

        blueprint = {
            "schema_version": SCHEMA_VERSION,
            "artifact": "design-blueprint",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": {"agent": AGENT_NAME, "version": AGENT_VERSION},
            "inputs": self.build_inputs(requirements, matrix),
            "project_name": requirements.get("project", {}).get("name") or "待确认项目名称",
            "architecture_layers": architecture_layers,
            "modules": modules,
            "sections": sections,
            "diagram_plan": diagram_plan,
            "coverage_map": coverage_map,
            "assumptions": [
                "Design Agent 只规划结构、模块、章节和图表，不生成最终正文或 Mermaid 源码。",
                "高风险事实、人员资质、服务承诺和商务条款在正文阶段继续保留 REVIEW 或 CONFIRM 状态。",
            ],
            "warnings": self.warnings,
        }

        diagram_plan_json = {
            "schema_version": SCHEMA_VERSION,
            "artifact": "diagram-plan",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": {"agent": AGENT_NAME, "version": AGENT_VERSION},
            "inputs": [
                {"artifact": "design-blueprint", "path": "output/records/design-blueprint.json", "schema_version": SCHEMA_VERSION}
            ],
            "diagrams": diagram_plan,
        }

        self.validate_blueprint(blueprint, requirements)

        staging_dir = self.workspace / "working" / "agent-system" / "staging" / "design" / self.run_id
        published_dir = self.workspace / "working" / "agent-system" / "published" / "design" / self.run_id
        for directory in (staging_dir, published_dir, self.output_dir):
            directory.mkdir(parents=True, exist_ok=True)

        outputs = {
            "design-blueprint.json": json.dumps(blueprint, ensure_ascii=False, indent=2) + "\n",
            "section-plan.md": self.render_section_plan(blueprint),
            "diagram-plan.json": json.dumps(diagram_plan_json, ensure_ascii=False, indent=2) + "\n",
            "diagram-plan.md": self.render_diagram_plan(blueprint),
        }
        for name, content in outputs.items():
            (staging_dir / name).write_text(content, encoding="utf-8")
        for name in outputs:
            shutil.copy2(staging_dir / name, published_dir / name)
            shutil.copy2(staging_dir / name, self.output_dir / name)

        return {"staging_dir": staging_dir, "published_dir": published_dir, "output_dir": self.output_dir}

    def load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"缺少 Design Agent 必需输入：{path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - convert to stage failure
            raise RuntimeError(f"无法解析 JSON 输入：{path}") from exc

    def extract_placeholders(self) -> list[str]:
        template = self.find_template()
        if template is None:
            self.warnings.append("未找到投标方案模板，章节 placeholder 将使用内置默认值。")
            return []
        try:
            with zipfile.ZipFile(template) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
        except Exception as exc:  # noqa: BLE001
            self.warnings.append(f"无法读取模板占位符：{self.relative(template)}；{exc}")
            return []
        full_text = "".join(node.text or "" for node in root.findall(".//w:t", WORD_NS))
        placeholders = list(dict.fromkeys(re.findall(r"【[^】]+】", full_text)))
        if not placeholders:
            self.warnings.append(f"模板 {self.relative(template)} 未识别到占位符。")
        return placeholders

    def find_template(self) -> Path | None:
        candidates = [path for path in self.template_dir.glob("*.docx") if "优化前" not in path.name]
        return candidates[0] if candidates else None

    def build_inputs(self, requirements: dict[str, Any], matrix: dict[str, Any]) -> list[dict[str, str]]:
        inputs = [
            {"artifact": "requirements", "path": "output/records/requirements.json", "schema_version": str(requirements.get("schema_version", "unknown"))},
            {"artifact": "requirements-matrix", "path": "output/records/requirements-matrix.json", "schema_version": str(matrix.get("schema_version", "unknown"))},
        ]
        if self.placeholders:
            inputs.append({"artifact": "template-placeholder-spec", "path": self.relative(self.find_template() or self.template_dir), "schema_version": "template-docx"})
        rules = self.workspace / "docs" / "contracts" / "validation-rules.md"
        if rules.exists():
            inputs.append({"artifact": "generation-rules", "path": self.relative(rules), "schema_version": "markdown"})
        return inputs

    def generate_target_blueprint_with_api(
        self,
        requirements: dict[str, Any],
        req_items: list[dict[str, Any]],
        scoring_items: list[dict[str, Any]],
        writing_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        function_groups = self.primary_function_groups(req_items)
        if not function_groups:
            raise RuntimeError("Design Agent 无法识别一级功能，不能生成架构与功能图表计划。")

        payload = {
            "task": "generate_design_blueprint",
            "agent": AGENT_NAME,
            "schema_version": SCHEMA_VERSION,
            "project": requirements.get("project", {}),
            "technical_source_text": self.technical_source_text(requirements),
            "requirements": [self.design_requirement_brief(item) for item in req_items],
            "scoring_items": [self.design_scoring_brief(item) for item in scoring_items],
            "writing_requirements": [self.design_writing_brief(item) for item in writing_items],
            "template_placeholders": self.placeholders,
            "primary_function_groups": function_groups,
            "target_placeholders": sorted(TARGET_PLACEHOLDERS),
            "rules": [
                "只生成系统架构、架构图说明、功能设计总述、功能设计章节相关蓝图，不生成建设背景正文，不改变需求分析搬运逻辑。",
                "architecture_layers、modules、sections、diagram_plan 必须完全根据技术要求抽象，不得套用海图、ECDIS、AIS、雷达、航线、NMEA、船舶等无关领域模板。",
                "sections 至少覆盖四个占位符：【GEN:总体架构设计】、【GEN:架构图说明】、【GEN:功能设计总述】、【GEN:功能设计章节】。",
                "diagram_plan 必须包含 1 张系统总体架构图，kind=architecture；并为 primary_function_groups 中每个一级功能生成 1 张流程图，kind=function_flow。",
                "每张图必须绑定 source_requirement_ids，功能流程图必须绑定该一级功能对应的全部或主要 requirement_id。",
                "保持现有 schema 字段名称：layer_id/name/responsibility/source_requirement_ids；module_id/name/responsibility/layer_ids/source_requirement_ids/related_scoring_item_ids；section_id/placeholder/title/content_type/source_requirement_ids/related_scoring_item_ids/writing_requirement_ids/module_ids/status；diagram_id/title/kind/purpose/layout_hint/source_requirement_ids/related_section_ids/related_module_ids。",
                "返回 JSON only，不要 Markdown 代码块。",
            ],
            "output_contract": {
                "architecture_layers": [],
                "modules": [],
                "sections": [],
                "diagram_plan": [],
            },
        }
        response = call_llm_api(payload)
        return self.normalize_api_blueprint(response, req_items, scoring_items, writing_items, function_groups)

    def normalize_api_blueprint(
        self,
        response: Any,
        req_items: list[dict[str, Any]],
        scoring_items: list[dict[str, Any]],
        writing_items: list[dict[str, Any]],
        function_groups: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise RuntimeError("Design Agent API 必须返回 JSON object。")
        valid_req_ids = {item["requirement_id"] for item in req_items if item.get("requirement_id")}
        valid_score_ids = {item["scoring_item_id"] for item in scoring_items if item.get("scoring_item_id")}
        valid_writing_ids = {item["writing_requirement_id"] for item in writing_items if item.get("writing_requirement_id")}

        layers = self.normalize_api_layers(response.get("architecture_layers"), valid_req_ids)
        modules = self.normalize_api_modules(response.get("modules"), layers, valid_req_ids, valid_score_ids)
        sections = self.normalize_api_sections(response.get("sections"), valid_req_ids, valid_score_ids, valid_writing_ids)
        diagrams = self.normalize_api_diagrams(response.get("diagram_plan"), valid_req_ids, function_groups)

        combined_text = json.dumps(
            {"architecture_layers": layers, "modules": modules, "sections": sections, "diagram_plan": diagrams},
            ensure_ascii=False,
        )
        pollution = [term for term in FORBIDDEN_DOMAIN_TERMS if term in combined_text]
        if pollution:
            raise RuntimeError(f"Design Agent API 输出存在无关领域污染词：{', '.join(sorted(set(pollution)))}")
        return {"architecture_layers": layers, "modules": modules, "sections": sections, "diagram_plan": diagrams}

    def normalize_api_layers(self, raw_layers: Any, valid_req_ids: set[str]) -> list[dict[str, Any]]:
        if not isinstance(raw_layers, list) or not raw_layers:
            raise RuntimeError("Design Agent API 未返回 architecture_layers。")
        layers = []
        for index, raw in enumerate(raw_layers, 1):
            if not isinstance(raw, dict):
                continue
            layer_id = str(raw.get("layer_id") or f"L{index:03d}")
            if not re.match(r"^L[0-9]{3}$", layer_id):
                layer_id = f"L{index:03d}"
            source_ids = self.unique_valid_req_ids(raw.get("source_requirement_ids", []))
            source_ids = [req_id for req_id in source_ids if req_id in valid_req_ids]
            layers.append(
                {
                    "layer_id": layer_id,
                    "name": self.required_text(raw, "name", f"架构层{index}"),
                    "responsibility": self.required_text(raw, "responsibility", "负责系统相关能力的分层承载、接口协同和运行支撑。"),
                    "source_requirement_ids": source_ids,
                }
            )
        if not layers:
            raise RuntimeError("Design Agent API 返回的 architecture_layers 无可用项。")
        return layers

    def normalize_api_modules(
        self,
        raw_modules: Any,
        layers: list[dict[str, Any]],
        valid_req_ids: set[str],
        valid_score_ids: set[str],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_modules, list) or not raw_modules:
            raise RuntimeError("Design Agent API 未返回 modules。")
        layer_ids = {layer["layer_id"] for layer in layers}
        modules = []
        for index, raw in enumerate(raw_modules, 1):
            if not isinstance(raw, dict):
                continue
            module_id = str(raw.get("module_id") or f"M{index:03d}")
            if not re.match(r"^M[0-9]{3}$", module_id):
                module_id = f"M{index:03d}"
            source_ids = [req_id for req_id in self.unique_valid_req_ids(raw.get("source_requirement_ids", [])) if req_id in valid_req_ids]
            related_scores = [score_id for score_id in self.unique_score_ids(raw.get("related_scoring_item_ids", [])) if score_id in valid_score_ids]
            module_layer_ids = [layer_id for layer_id in self.unique(raw.get("layer_ids", [])) if layer_id in layer_ids] or [layers[min(index - 1, len(layers) - 1)]["layer_id"]]
            modules.append(
                {
                    "module_id": module_id,
                    "name": self.required_text(raw, "name", f"功能模块{index}"),
                    "responsibility": self.required_text(raw, "responsibility", "负责对应功能的输入处理、业务规则、输出结果和质量控制。"),
                    "layer_ids": module_layer_ids,
                    "source_requirement_ids": source_ids,
                    "related_scoring_item_ids": related_scores,
                    "suggested_diagram_ids": [],
                }
            )
        if not modules:
            raise RuntimeError("Design Agent API 返回的 modules 无可用项。")
        return modules

    def normalize_api_sections(
        self,
        raw_sections: Any,
        valid_req_ids: set[str],
        valid_score_ids: set[str],
        valid_writing_ids: set[str],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_sections, list):
            raise RuntimeError("Design Agent API 未返回 sections 数组。")
        sections = []
        for index, raw in enumerate(raw_sections, 1):
            if not isinstance(raw, dict):
                continue
            placeholder = str(raw.get("placeholder") or "")
            if placeholder not in TARGET_PLACEHOLDERS:
                continue
            content_type = str(raw.get("content_type") or self.target_content_type(placeholder))
            source_ids = [req_id for req_id in self.unique_valid_req_ids(raw.get("source_requirement_ids", [])) if req_id in valid_req_ids]
            if not source_ids and placeholder != "【GEN:架构图说明】":
                raise RuntimeError(f"Design Agent API 章节 {placeholder} 缺少来源需求 ID。")
            sections.append(
                {
                    "section_id": str(raw.get("section_id") or f"SEC{index:03d}"),
                    "placeholder": placeholder,
                    "title": self.required_text(raw, "title", placeholder.strip("【】").split(":", 1)[-1]),
                    "content_type": content_type,
                    "source_requirement_ids": source_ids,
                    "related_scoring_item_ids": [score_id for score_id in self.unique_score_ids(raw.get("related_scoring_item_ids", [])) if score_id in valid_score_ids],
                    "writing_requirement_ids": [writing_id for writing_id in self.unique_writing_ids(raw.get("writing_requirement_ids", [])) if writing_id in valid_writing_ids],
                    "module_ids": [],
                    "status": str(raw.get("status") or "planned"),
                }
            )
        missing = TARGET_PLACEHOLDERS - {section["placeholder"] for section in sections}
        if missing:
            raise RuntimeError(f"Design Agent API 未返回目标章节：{', '.join(sorted(missing))}")
        return sections

    def normalize_api_diagrams(
        self,
        raw_diagrams: Any,
        valid_req_ids: set[str],
        function_groups: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if not isinstance(raw_diagrams, list):
            raise RuntimeError("Design Agent API 未返回 diagram_plan 数组。")
        diagrams = []
        for index, raw in enumerate(raw_diagrams, 1):
            if not isinstance(raw, dict):
                continue
            diagram_id = str(raw.get("diagram_id") or f"DG{index:03d}")
            if not re.match(r"^DG[0-9]{3}$", diagram_id):
                diagram_id = f"DG{index:03d}"
            kind = str(raw.get("kind") or "other")
            source_ids = [req_id for req_id in self.unique_valid_req_ids(raw.get("source_requirement_ids", [])) if req_id in valid_req_ids]
            if not source_ids:
                raise RuntimeError(f"Design Agent API 图表 {diagram_id} 缺少来源需求 ID。")
            diagrams.append(
                {
                    "diagram_id": diagram_id,
                    "title": self.required_text(raw, "title", diagram_id),
                    "kind": kind,
                    "purpose": self.required_text(raw, "purpose", "说明系统相关模块、数据流或流程关系。"),
                    "layout_hint": str(raw.get("layout_hint") or "flowchart TB"),
                    "source_requirement_ids": source_ids,
                    "related_section_ids": self.unique(raw.get("related_section_ids", [])),
                    "related_module_ids": self.unique(raw.get("related_module_ids", [])),
                }
            )
        architecture_count = sum(1 for diagram in diagrams if diagram["kind"] == "architecture" and ("总体架构" in diagram["title"] or "架构" in diagram["title"]))
        function_count = sum(1 for diagram in diagrams if diagram["kind"] == "function_flow")
        if architecture_count < 1:
            raise RuntimeError("Design Agent API 图表计划缺少系统总体架构图。")
        if function_count < len(function_groups):
            raise RuntimeError(f"Design Agent API 图表计划缺少一级功能流程图：{function_count}/{len(function_groups)}。")
        return diagrams

    def primary_function_groups(self, requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[str, list[str]] = {}
        for item in requirements:
            source = item.get("source") or {}
            locator = str(source.get("locator") or "")
            title = locator.split("/L", 1)[0].split("/", 1)[0].strip() if locator else ""
            title = re.sub(r"^[0-9]+[.、）)]\s*", "", title).strip() or str(item.get("title") or "").strip()
            req_id = item.get("requirement_id")
            if not title or not req_id:
                continue
            groups.setdefault(title, []).append(req_id)
        return [
            {"title": title, "source_requirement_ids": self.unique_valid_req_ids(req_ids)}
            for title, req_ids in groups.items()
            if req_ids
        ]

    def technical_source_text(self, requirements: dict[str, Any]) -> str:
        for source in requirements.get("source_documents", []):
            if source.get("kind") != "technical_requirements_markdown":
                continue
            path = self.resolve_workspace_path(str(source.get("path") or ""))
            if path.exists():
                return path.read_text(encoding="utf-8", errors="replace")[:12000]
        return "\n".join(str(item.get("text", "")) for item in requirements.get("requirements", []))

    def merge_target_sections(
        self,
        base_sections: list[dict[str, Any]],
        api_sections: list[dict[str, Any]],
        requirements: list[dict[str, Any]],
        scoring_items: list[dict[str, Any]],
        writing_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        by_placeholder = {section["placeholder"]: section for section in base_sections}
        next_index = self.next_section_index(base_sections)
        result = []
        for section in base_sections:
            if section["placeholder"] in TARGET_PLACEHOLDERS:
                replacement = next((item for item in api_sections if item["placeholder"] == section["placeholder"]), None)
                if replacement:
                    merged = dict(section)
                    merged.update({key: value for key, value in replacement.items() if key != "section_id"})
                    merged["section_id"] = section["section_id"]
                    result.append(merged)
                continue
            result.append(section)
        existing = {section["placeholder"] for section in result}
        for api_section in api_sections:
            if api_section["placeholder"] in existing:
                continue
            section = dict(api_section)
            section["section_id"] = f"SEC{next_index:03d}"
            next_index += 1
            result.append(section)
        missing = TARGET_PLACEHOLDERS - {section["placeholder"] for section in result}
        if missing:
            raise RuntimeError(f"目标章节未进入设计蓝图：{', '.join(sorted(missing))}")
        return result

    def attach_api_modules_to_sections(self, sections: list[dict[str, Any]], modules: list[dict[str, Any]]) -> None:
        for section in sections:
            req_ids = set(section.get("source_requirement_ids", []))
            section["module_ids"] = [
                module["module_id"]
                for module in modules
                if req_ids & set(module.get("source_requirement_ids", []))
            ]

    def retarget_diagram_sections(self, diagrams: list[dict[str, Any]], sections: list[dict[str, Any]]) -> None:
        architecture_sections = [
            section["section_id"]
            for section in sections
            if section.get("placeholder") in {"【GEN:总体架构设计】", "【GEN:总体架构图】", "【GEN:架构图说明】"}
        ]
        function_sections = [
            section["section_id"]
            for section in sections
            if section.get("placeholder") in {"【GEN:功能设计总述】", "【GEN:功能设计章节】"}
        ]
        known_section_ids = {section["section_id"] for section in sections}
        known_module_ids = {
            module_id
            for section in sections
            for module_id in section.get("module_ids", [])
        }
        for diagram in diagrams:
            existing_sections = [section_id for section_id in diagram.get("related_section_ids", []) if section_id in known_section_ids]
            if diagram.get("kind") == "architecture":
                diagram["related_section_ids"] = self.unique(existing_sections + architecture_sections)
            elif diagram.get("kind") == "function_flow":
                diagram["related_section_ids"] = self.unique(existing_sections + function_sections)
            else:
                diagram["related_section_ids"] = existing_sections
            diagram["related_module_ids"] = [module_id for module_id in diagram.get("related_module_ids", []) if module_id in known_module_ids]

    @staticmethod
    def target_content_type(placeholder: str) -> str:
        if placeholder == "【GEN:总体架构图】":
            return "diagram_reference"
        if placeholder == "【GEN:功能设计章节】":
            return "dynamic_sections"
        return "generated_paragraphs"

    @staticmethod
    def required_text(raw: dict[str, Any], field: str, fallback: str) -> str:
        value = str(raw.get(field) or "").strip()
        return value or fallback

    @staticmethod
    def design_requirement_brief(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "requirement_id": item.get("requirement_id"),
            "category": item.get("category"),
            "title": item.get("title"),
            "text": item.get("text"),
            "keywords": item.get("keywords", []),
            "target_sections": item.get("target_sections", []),
        }

    @staticmethod
    def design_scoring_brief(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "scoring_item_id": item.get("scoring_item_id"),
            "title": item.get("title"),
            "text": item.get("text"),
            "response_section": item.get("response_section"),
        }

    @staticmethod
    def design_writing_brief(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "writing_requirement_id": item.get("writing_requirement_id"),
            "title": item.get("title"),
            "text": item.get("text"),
            "target_sections": item.get("target_sections", []),
        }

    @staticmethod
    def delivery_as_requirements(delivery_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        converted = []
        for item in delivery_items:
            converted.append(
                {
                    "requirement_id": item.get("delivery_id"),
                    "category": "delivery",
                    "title": item.get("name", item.get("delivery_id", "")),
                    "text": item.get("name", ""),
                    "keywords": ["交付", "验收"],
                    "target_sections": ["交付方案", "项目实施计划"],
                    "mandatory": True,
                    "need_diagram": False,
                    "status": item.get("status", "extracted"),
                    "risk_level": "normal",
                }
            )
        return converted

    def build_layers(self, requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
        layer_specs = [
            ("L001", "显示交互与态势呈现层", "负责电子海图渲染、昼夜配色、图层控制、AIS/雷达/ARPA目标叠加、HMI操作和告警态势呈现，是船员直接使用的前端能力层。", {"显示", "HMI", "界面", "海图", "态势", "亮度", "图层", "AIS", "雷达", "ARPA"}),
            ("L002", "导航业务与安全决策层", "承载航线规划、航路监控、安全等深线、水深告警、偏航监控、航行风险判断和业务规则编排，将传感器与海图数据转化为导航决策支撑。", {"航线", "告警", "监控", "安全", "规划", "配置", "导航", "等深线", "水深"}),
            ("L003", "数据接入与标准化处理层", "负责S-57/S-63海图、S-52显示库、雷达回波、AIS/ARPA、NMEA导航数据、文件导入和数据库读写的统一接入、解析、坐标/时间对齐与质量校验。", {"数据", "接口", "AIS", "雷达", "ARPA", "NMEA", "数据库", "文件", "传感器", "S-57", "S-63", "S-52"}),
            ("L004", "平台运行与部署支撑层", "支撑龙芯、飞腾、瑞芯微等硬件平台以及麒麟、Ubuntu等操作系统上的跨平台部署，提供模块化运行、多设备优先级协同、性能响应和日志诊断能力。", {"平台", "部署", "国产化", "性能", "跨平台", "协同", "运行", "初始化"}),
            ("L005", "质量安全与交付保障层", "覆盖IEC/IHO/GJB等标准符合性、权限保密、安全测试、可测试性、质量监督、验收交付和售后保障，形成可追溯的工程质量闭环。", {"质量", "保密", "权限", "验收", "交付", "售后", "测试", "标准", "履约"}),
        ]
        layers = []
        covered: set[str] = set()
        for layer_id, name, responsibility, keywords in layer_specs:
            req_ids = [item["requirement_id"] for item in requirements if self.matches_keywords(item, keywords)]
            if layer_id == "L005":
                req_ids.extend(
                    item["requirement_id"]
                    for item in requirements
                    if item.get("category") in {"technical_quality", "business", "delivery"} or item.get("risk_level") in {"high", "critical"}
                )
            req_ids = self.unique_valid_req_ids(req_ids)
            covered.update(req_ids)
            layers.append(
                {
                    "layer_id": layer_id,
                    "name": name,
                    "responsibility": responsibility,
                    "source_requirement_ids": req_ids[:60],
                }
            )
        remaining = self.unique_valid_req_ids(item["requirement_id"] for item in requirements if item["requirement_id"] not in covered)
        if remaining:
            layers[-1]["source_requirement_ids"] = self.unique_valid_req_ids(layers[-1]["source_requirement_ids"] + remaining)
        return layers

    def build_modules(
        self,
        requirements: list[dict[str, Any]],
        scoring_items: list[dict[str, Any]],
        layers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in requirements:
            groups[self.module_key(item)].append(item)

        layer_index = {layer["layer_id"]: set(layer["source_requirement_ids"]) for layer in layers}
        score_by_req: dict[str, list[str]] = defaultdict(list)
        for scoring in scoring_items:
            for req_id in scoring.get("related_requirement_ids", []):
                score_by_req[req_id].append(scoring["scoring_item_id"])

        modules = []
        for index, (key, items) in enumerate(sorted(groups.items(), key=lambda pair: self.group_sort_key(pair[1])), 1):
            req_ids = self.unique_valid_req_ids(item["requirement_id"] for item in items)
            related_scores = self.unique_score_ids(score_id for req_id in req_ids for score_id in score_by_req.get(req_id, []))
            layer_ids = [
                layer_id
                for layer_id, layer_req_ids in layer_index.items()
                if set(req_ids) & layer_req_ids
            ] or ["L005"]
            modules.append(
                {
                    "module_id": f"M{index:03d}",
                    "name": key,
                    "responsibility": self.module_responsibility(key, items),
                    "layer_ids": layer_ids,
                    "source_requirement_ids": req_ids,
                    "related_scoring_item_ids": related_scores,
                    "suggested_diagram_ids": [],
                }
            )

        unlinked_scores = [score for score in scoring_items if not score.get("related_requirement_ids")]
        for score in unlinked_scores:
            module = self.find_module_for_score(modules, score)
            if module:
                module["related_scoring_item_ids"] = self.unique_score_ids(module.get("related_scoring_item_ids", []) + [score["scoring_item_id"]])
        return modules

    def build_sections(
        self,
        requirements: list[dict[str, Any]],
        scoring_items: list[dict[str, Any]],
        writing_items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        sections = []
        for index, spec in enumerate(SECTION_TARGETS, 1):
            req_ids = self.source_ids_for_section(spec, requirements)
            score_ids = self.score_ids_for_section(spec, scoring_items)
            writing_ids = self.writing_ids_for_section(spec["title"], spec.get("match_sections", set()), writing_items)
            if not req_ids and not score_ids and not writing_ids:
                continue
            status = self.section_status(req_ids, score_ids, writing_ids, requirements, scoring_items, writing_items, spec["content_type"])
            sections.append(
                {
                    "section_id": f"SEC{index:03d}",
                    "placeholder": spec["placeholder"],
                    "title": spec["title"],
                    "content_type": spec["content_type"],
                    "source_requirement_ids": req_ids,
                    "related_scoring_item_ids": score_ids,
                    "writing_requirement_ids": writing_ids,
                    "module_ids": [],
                    "status": status,
                }
            )
        self.append_writing_fallback_section(sections, writing_items)
        self.append_template_placeholder_sections(sections, requirements, scoring_items, writing_items)
        self.attach_modules_to_sections(sections, requirements)
        return sections

    def append_writing_fallback_section(self, sections: list[dict[str, Any]], writing_items: list[dict[str, Any]]) -> None:
        fallback_ids = [
            item["writing_requirement_id"]
            for item in writing_items
            if "方案撰写要求专项响应" in item.get("target_sections", []) or item.get("mapping_confidence") == "low"
        ]
        fallback_ids = self.unique_writing_ids(fallback_ids)
        if not fallback_ids:
            return
        next_index = self.next_section_index(sections)
        sections.append(
            {
                "section_id": f"SEC{next_index:03d}",
                "placeholder": "【REVIEW:方案撰写要求专项响应】",
                "title": "方案撰写要求专项响应",
                "content_type": "review_text",
                "source_requirement_ids": self.unique_valid_req_ids(
                    req_id
                    for section in sections
                    for req_id in section.get("source_requirement_ids", [])
                )[:12],
                "related_scoring_item_ids": [],
                "writing_requirement_ids": fallback_ids,
                "module_ids": [],
                "status": "review_required",
            }
        )
        self.warnings.append("部分方案撰写要求为低置信度自动映射，已进入专项响应章节并要求复核。")

    def append_template_placeholder_sections(
        self,
        sections: list[dict[str, Any]],
        requirements: list[dict[str, Any]],
        scoring_items: list[dict[str, Any]],
        writing_items: list[dict[str, Any]],
    ) -> None:
        covered = {section["placeholder"] for section in sections}
        next_index = self.next_section_index(sections)
        for placeholder in self.placeholders:
            if placeholder in covered:
                continue
            kind, label = self.parse_placeholder(placeholder)
            if kind not in {"GEN", "REVIEW"}:
                continue

            req_ids = self.source_ids_for_placeholder(label, requirements)
            score_ids = self.score_ids_for_placeholder(label, scoring_items)
            writing_ids = self.writing_ids_for_section(label, {label}, writing_items)
            if not req_ids and not score_ids and not writing_ids:
                req_ids = self.unique_valid_req_ids(item["requirement_id"] for item in requirements[:8])
            content_type = "review_text" if kind == "REVIEW" else "generated_paragraphs"
            status = self.section_status(req_ids, score_ids, writing_ids, requirements, scoring_items, writing_items, content_type)
            sections.append(
                {
                    "section_id": f"SEC{next_index:03d}",
                    "placeholder": placeholder,
                    "title": label,
                    "content_type": content_type,
                    "source_requirement_ids": req_ids,
                    "related_scoring_item_ids": score_ids,
                    "writing_requirement_ids": writing_ids,
                    "module_ids": [],
                    "status": status,
                }
            )
            covered.add(placeholder)
            next_index += 1
            self.warnings.append(f"模板占位符 {placeholder} 未在内置章节目标中定义，已按模板兜底生成 section。")

    def build_diagram_plan(self, requirements: list[dict[str, Any]], sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        diagram_plan = []
        section_by_req: dict[str, list[str]] = defaultdict(list)
        for section in sections:
            for req_id in section["source_requirement_ids"]:
                section_by_req[req_id].append(section["section_id"])

        for index, template in enumerate(DIAGRAM_TEMPLATES, 1):
            req_ids = self.source_ids_for_diagram(template, requirements)
            if not req_ids:
                continue
            section_ids = self.unique(section_id for req_id in req_ids for section_id in section_by_req.get(req_id, []))
            selector_sections = template.get("selectors", {}).get("sections", set())
            direct_section_ids = [
                section["section_id"]
                for section in sections
                if any(selector in section.get("title", "") or section.get("title", "") in selector for selector in selector_sections)
            ]
            if template.get("title") == "总体架构蓝图":
                direct_section_ids.extend(section["section_id"] for section in sections if "架构" in section.get("title", ""))
            section_ids = self.unique(direct_section_ids + section_ids)
            direct_req_ids = [
                req_id
                for section in sections
                if section["section_id"] in set(direct_section_ids)
                for req_id in section.get("source_requirement_ids", [])
            ]
            req_ids = self.unique_valid_req_ids(direct_req_ids + req_ids)[: template["limit"]]
            diagram_plan.append(
                {
                    "diagram_id": f"DG{index:03d}",
                    "title": template["title"],
                    "kind": template["kind"],
                    "purpose": template["purpose"],
                    "source_requirement_ids": req_ids,
                    "related_section_ids": section_ids,
                    "layout_hint": template["layout_hint"],
                }
            )
        next_index = len(diagram_plan) + 1
        for requirement in requirements:
            req_id = str(requirement.get("requirement_id", ""))
            if not REQ_ID_RE.match(req_id) or requirement.get("category") != "technical_function":
                continue
            section_ids = self.unique(section_by_req.get(req_id, []))
            diagram_plan.append(
                {
                    "diagram_id": f"DG{next_index:03d}",
                    "title": f"{req_id} {requirement.get('title', '功能要求')}功能流程图",
                    "kind": "function_flow",
                    "purpose": f"说明{requirement.get('title', req_id)}的输入、处理、输出与异常记录流程。",
                    "source_requirement_ids": [req_id],
                    "related_section_ids": section_ids,
                    "layout_hint": "flowchart TB",
                }
            )
            next_index += 1
        return diagram_plan

    @staticmethod
    def attach_diagrams_to_modules(modules: list[dict[str, Any]], diagram_plan: list[dict[str, Any]]) -> None:
        for module in modules:
            module_req_ids = set(module["source_requirement_ids"])
            module["suggested_diagram_ids"] = [
                diagram["diagram_id"]
                for diagram in diagram_plan
                if module_req_ids & set(diagram["source_requirement_ids"])
            ][:3]

    def attach_modules_to_sections(self, sections: list[dict[str, Any]], requirements: list[dict[str, Any]]) -> None:
        req_to_module = {}
        for index, (key, items) in enumerate(
            sorted(defaultdict(list, ((self.module_key(item), []) for item in requirements)).items()), 1
        ):
            _ = key, items, index
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in requirements:
            groups[self.module_key(item)].append(item)
        module_ids_by_req: dict[str, str] = {}
        for index, (_key, items) in enumerate(sorted(groups.items(), key=lambda pair: self.group_sort_key(pair[1])), 1):
            for item in items:
                module_ids_by_req[item["requirement_id"]] = f"M{index:03d}"
        req_to_module.update(module_ids_by_req)
        for section in sections:
            section["module_ids"] = self.unique(
                req_to_module[req_id]
                for req_id in section["source_requirement_ids"]
                if req_id in req_to_module
            )

    def build_coverage_map(
        self,
        requirements: list[dict[str, Any]],
        scoring_items: list[dict[str, Any]],
        delivery_items: list[dict[str, Any]],
        writing_items: list[dict[str, Any]],
        modules: list[dict[str, Any]],
        sections: list[dict[str, Any]],
        diagrams: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        coverage = []
        for item in requirements + delivery_items:
            source_id = item["requirement_id"]
            refs = []
            refs.extend({"kind": "section", "id": section["section_id"]} for section in sections if source_id in section["source_requirement_ids"])
            refs.extend({"kind": "module", "id": module["module_id"]} for module in modules if source_id in module["source_requirement_ids"])
            refs.extend({"kind": "diagram", "id": diagram["diagram_id"]} for diagram in diagrams if source_id in diagram["source_requirement_ids"])
            if item.get("status") == "review_required":
                refs.append({"kind": "review", "id": source_id})
            if item.get("status") == "confirm_required":
                refs.append({"kind": "confirm", "id": source_id})
            coverage.append(
                {
                    "source_id": source_id,
                    "covered_by": refs,
                    "coverage_status": self.coverage_status(item.get("status", "extracted"), refs),
                }
            )

        for writing in writing_items:
            source_id = writing["writing_requirement_id"]
            refs = []
            refs.extend({"kind": "section", "id": section["section_id"]} for section in sections if source_id in section.get("writing_requirement_ids", []))
            if writing.get("status") == "review_required" or writing.get("mapping_confidence") == "low":
                refs.append({"kind": "review", "id": source_id})
            coverage.append(
                {
                    "source_id": source_id,
                    "covered_by": refs,
                    "coverage_status": self.coverage_status(writing.get("status", "extracted"), refs),
                }
            )

        for scoring in scoring_items:
            source_id = scoring["scoring_item_id"]
            refs = []
            refs.extend({"kind": "section", "id": section["section_id"]} for section in sections if source_id in section.get("related_scoring_item_ids", []))
            refs.extend({"kind": "module", "id": module["module_id"]} for module in modules if source_id in module.get("related_scoring_item_ids", []))
            if scoring.get("status") == "review_required":
                refs.append({"kind": "review", "id": source_id})
            if scoring.get("status") == "confirm_required":
                refs.append({"kind": "confirm", "id": source_id})
            coverage.append(
                {
                    "source_id": source_id,
                    "covered_by": refs,
                    "coverage_status": self.coverage_status(scoring.get("status", "extracted"), refs),
                }
            )
        return coverage

    def source_ids_for_section(self, spec: dict[str, Any], requirements: list[dict[str, Any]]) -> list[str]:
        result = []
        for item in requirements:
            target_sections = set(item.get("target_sections", []))
            category = item.get("category")
            if target_sections & spec.get("match_sections", set()) or category in spec.get("match_categories", set()):
                if self.keyword_filter_passes(spec, item):
                    result.append(item["requirement_id"])
        return self.unique_valid_req_ids(result)

    def score_ids_for_section(self, spec: dict[str, Any], scoring_items: list[dict[str, Any]]) -> list[str]:
        result = []
        for item in scoring_items:
            response_section = item.get("response_section", "")
            title = item.get("title", "")
            if response_section in spec.get("match_scores", set()) or title in spec.get("match_scores", set()):
                result.append(item["scoring_item_id"])
        return self.unique_score_ids(result)

    def writing_ids_for_section(
        self,
        title: str,
        match_sections: set[str],
        writing_items: list[dict[str, Any]],
    ) -> list[str]:
        result = []
        section_tokens = set(match_sections) | {title}
        for item in writing_items:
            target_sections = set(item.get("target_sections", []))
            if target_sections & section_tokens:
                result.append(item["writing_requirement_id"])
                continue
            text = " ".join([item.get("title", ""), item.get("text", ""), " ".join(item.get("keywords", []))])
            if any(token and token in text for token in section_tokens):
                result.append(item["writing_requirement_id"])
        return self.unique_writing_ids(result)

    def source_ids_for_placeholder(self, label: str, requirements: list[dict[str, Any]]) -> list[str]:
        label_tokens = self.placeholder_tokens(label)
        result = []
        for item in requirements:
            text = " ".join(
                [
                    item.get("title", ""),
                    item.get("text", ""),
                    " ".join(item.get("keywords", [])),
                    " ".join(item.get("target_sections", [])),
                    item.get("category", ""),
                ]
            )
            if any(token and token in text for token in label_tokens):
                result.append(item["requirement_id"])
        if not result:
            result = [item["requirement_id"] for item in requirements if item.get("category") in self.placeholder_categories(label)]
        return self.unique_valid_req_ids(result)[:40]

    def score_ids_for_placeholder(self, label: str, scoring_items: list[dict[str, Any]]) -> list[str]:
        label_tokens = self.placeholder_tokens(label)
        result = []
        for item in scoring_items:
            text = " ".join([item.get("title", ""), item.get("text", ""), item.get("response_section", "")])
            if any(token and token in text for token in label_tokens):
                result.append(item["scoring_item_id"])
        return self.unique_score_ids(result)

    @staticmethod
    def parse_placeholder(placeholder: str) -> tuple[str, str]:
        match = re.match(r"^【(?P<kind>GEN|COPY|REVIEW|CONFIRM):(?P<label>[^】]+)】$", placeholder)
        if not match:
            return "", placeholder.strip("【】")
        return match.group("kind"), match.group("label").strip()

    @staticmethod
    def placeholder_tokens(label: str) -> set[str]:
        token_map = {
            "编写目的": {"需求", "建设", "技术方案", "项目"},
            "数据库": {"数据库", "数据", "接口", "海图", "文件", "NMEA"},
            "保障": {"保障", "维修", "培训", "售后", "服务", "交付"},
            "质量": {"质量", "测试", "验收", "可靠", "安全"},
            "资质": {"资质", "证明", "人员", "证书"},
            "项目进度": {"项目", "实施", "进度", "交付", "工期"},
            "交付物": {"交付", "验收", "成果"},
            "应急": {"应急", "支援", "服务", "保障"},
            "定期跟踪": {"跟踪", "服务", "售后", "维护"},
        }
        tokens = {label}
        for key, values in token_map.items():
            if key in label:
                tokens.update(values)
        for chunk in re.split(r"[与及、\s-]+", label):
            if chunk:
                tokens.add(chunk)
        return tokens

    @staticmethod
    def placeholder_categories(label: str) -> set[str]:
        if any(keyword in label for keyword in ("交付", "验收", "进度", "服务", "培训", "保障", "承诺", "应急", "跟踪")):
            return {"business", "delivery", "technical_quality"}
        if "数据库" in label or "数据" in label:
            return {"technical_function", "technical_quality"}
        if "质量" in label or "测试" in label:
            return {"technical_quality"}
        return {"technical_function", "technical_quality", "business", "delivery"}

    @staticmethod
    def next_section_index(sections: list[dict[str, Any]]) -> int:
        indexes = []
        for section in sections:
            match = re.match(r"^SEC([0-9]{3})$", str(section.get("section_id", "")))
            if match:
                indexes.append(int(match.group(1)))
        return (max(indexes) if indexes else 0) + 1

    def source_ids_for_diagram(self, template: dict[str, Any], requirements: list[dict[str, Any]]) -> list[str]:
        selectors = template["selectors"]
        result = []
        for item in requirements:
            target_sections = set(item.get("target_sections", []))
            category = item.get("category")
            if target_sections & selectors.get("sections", set()):
                result.append(item["requirement_id"])
            elif category in selectors.get("categories", set()):
                result.append(item["requirement_id"])
            elif self.matches_keywords(item, selectors.get("keywords", set())):
                result.append(item["requirement_id"])
        return self.unique_valid_req_ids(result)[: template["limit"]]

    @staticmethod
    def keyword_filter_passes(spec: dict[str, Any], item: dict[str, Any]) -> bool:
        keywords = spec.get("keyword_any")
        if not keywords:
            return True
        text = " ".join([item.get("title", ""), item.get("text", ""), " ".join(item.get("keywords", []))])
        return any(keyword in text for keyword in keywords)

    @staticmethod
    def matches_keywords(item: dict[str, Any], keywords: set[str]) -> bool:
        if not keywords:
            return False
        text = " ".join([item.get("title", ""), item.get("text", ""), " ".join(item.get("keywords", [])), " ".join(item.get("target_sections", []))])
        return any(keyword in text for keyword in keywords)

    def module_key(self, item: dict[str, Any]) -> str:
        category = item.get("category")
        raw_title = item.get("title", "")
        text = " ".join([raw_title, item.get("text", ""), " ".join(item.get("keywords", []))])
        if category == "technical_function":
            if any(keyword in text for keyword in ("海图", "S-57", "S-63", "S-52", "PresLib", "配色", "图层")):
                return "电子海图显示与图层控制"
            if any(keyword in text for keyword in ("AIS", "雷达", "ARPA", "目标", "本船")):
                return "目标信息融合与态势叠加"
            if any(keyword in text for keyword in ("航线", "航向点", "航段", "偏航")):
                return "航线规划与航行监控"
            if any(keyword in text for keyword in ("安全等深线", "水深", "告警", "预警", "避碰")):
                return "安全告警与风险预警"
            if any(keyword in text for keyword in ("个性化", "配置", "显示参数", "菜单", "快捷键", "触摸屏", "鼠标", "键盘", "轨迹球")):
                return "人机交互与个性化配置"
            if any(keyword in text for keyword in ("数据维护", "数据库", "接口", "NMEA", "文件", "日志", "读写")):
                return "数据维护与接口管理"
            if any(keyword in text for keyword in ("跨平台", "多设备", "模块化", "架构", "龙芯", "飞腾", "瑞芯微", "麒麟", "Ubuntu")):
                return "平台适配与模块化运行"
        title = self.clean_title(raw_title)
        if category == "technical_performance":
            return "性能与技术指标响应"
        if category == "technical_quality":
            if self.matches_keywords(item, {"安全", "保密", "权限"}):
                return "安全与保密控制"
            if self.matches_keywords(item, {"测试", "验收", "检测", "质量", "评价"}):
                return "质量监督与测试验收"
            if self.matches_keywords(item, {"部署", "运行", "国产化", "环境"}):
                return "部署运行与环境适配"
            if self.matches_keywords(item, {"标准", "规范", "合规"}):
                return "标准符合与合规控制"
            return "通用质量特性保障"
        if category == "business":
            return self.business_module_name(title)
        if category == "delivery":
            return "交付物与验收支撑"
        return title or "其他响应模块"

    @staticmethod
    def business_module_name(title: str) -> str:
        if any(keyword in title for keyword in ("交付", "时间", "地点")):
            return "项目实施与交付管理"
        if any(keyword in title for keyword in ("售后", "培训", "履约")):
            return "培训售后与履约保障"
        if any(keyword in title for keyword in ("知识产权", "保密")):
            return "商务保密与知识产权响应"
        if any(keyword in title for keyword in ("付款", "结算", "保证金")):
            return "商务条款与结算响应"
        return "商务条款响应"

    @staticmethod
    def module_responsibility(name: str, items: list[dict[str, Any]]) -> str:
        source_ids = "、".join(item.get("requirement_id", "") for item in items[:8] if item.get("requirement_id"))
        text = " ".join(item.get("text", "") for item in items)
        responsibility_map = {
            "电子海图显示与图层控制": "构建符合ECDIS相关标准的海图加载、符号化渲染、比例尺切换、昼夜配色和图层控制能力，支撑标准电子海图在不同航行场景下稳定呈现。",
            "目标信息融合与态势叠加": "接入雷达、AIS、ARPA和本船导航数据，完成坐标对齐、目标状态维护和OpenGL态势叠加，形成海图、目标、航行环境一致的综合显示能力。",
            "航线规划与航行监控": "提供航点、航段、航线编辑与监控机制，支撑手动/自动规划、航线校验、航行过程跟踪和偏航提示。",
            "安全告警与风险预警": "围绕安全等深线、水深、禁航区、偏航和目标风险构建规则判断与告警闭环，支撑风险发现、提示、确认和记录。",
            "人机交互与个性化配置": "面向船员高频操作设计菜单、快捷键、多输入设备和个性化参数保存机制，提升复杂航行场景下的可用性与操作效率。",
            "数据维护与接口管理": "负责海图、航线、日志、传感器接口和数据库读写的统一维护，提供数据校验、版本管理、接口隔离和可追溯记录。",
            "平台适配与模块化运行": "以分层解耦和模块化运行方式适配国产化软硬件环境、多设备协同和后续功能扩展，降低平台迁移与维护成本。",
            "性能与技术指标响应": "围绕初始化、响应时间、系统稳定性、资源占用和多设备协同等指标建立性能设计、监测和验证路径。",
            "安全与保密控制": "建立权限授权、网络安全部署、保密要求、操作审计和安全测试机制，控制正式运行环境中的访问与数据风险。",
            "质量监督与测试验收": "围绕标准符合性、测试接口、单元/集成/系统/验收测试和问题闭环建立质量控制机制。",
            "部署运行与环境适配": "支撑目标硬件、操作系统、网络环境和运行依赖的适配部署，明确配置、日志、诊断和运维边界。",
            "标准符合与合规控制": "将IEC、IHO、GB/T、GJB等标准要求落实到显示、数据保护、文档、测试和验收环节。",
            "通用质量特性保障": "围绕可靠性、维修性、测试性、安全性、环境适应性等质量属性建立设计约束和验证手段。",
            "项目实施与交付管理": "组织实施计划、阶段成果、验收配合和交付物管理，支撑项目过程可控。",
            "培训售后与履约保障": "规划培训、售后、应急支援和服务跟踪机制，保留需人工确认的服务承诺边界。",
            "商务保密与知识产权响应": "落实商务保密、知识产权归属和外包管理要求，避免无依据承诺。",
            "商务条款与结算响应": "响应付款、结算、履约保障金等商务条款，相关事实保持人工确认或复核。",
            "交付物与验收支撑": "明确外包验收资料、软件文档、测试资料和交付介质之间的对应关系。",
        }
        responsibility = responsibility_map.get(name)
        if not responsibility:
            sample_titles = "、".join(dict.fromkeys(item.get("title", "") for item in items if item.get("title"))).strip("、")
            responsibility = f"负责{sample_titles or name}相关能力的方案设计、接口边界、处理流程和质量控制。"
        if source_ids:
            responsibility += f" 来源依据包括{source_ids}"
            if len(items) > 8:
                responsibility += "等"
            responsibility += "。"
        if "OpenGL" in text and "OpenGL" not in responsibility:
            responsibility += " 其中图形叠加与显示渲染需体现OpenGL处理链路。"
        return responsibility

    @staticmethod
    def clean_title(value: str) -> str:
        value = re.sub(r"[：:；;，,。].*$", "", value).strip()
        value = re.sub(r"^[0-9]+[）).、\s]*", "", value)
        return value[:32] or "未命名模块"

    @staticmethod
    def group_sort_key(items: list[dict[str, Any]]) -> tuple[int, str]:
        first_id = items[0].get("requirement_id", "")
        prefix_order = {"Q": 0, "T": 1, "P": 2, "B": 3, "D": 4}
        return (prefix_order.get(first_id[:1], 9), first_id)

    @staticmethod
    def find_module_for_score(modules: list[dict[str, Any]], score: dict[str, Any]) -> dict[str, Any] | None:
        section = score.get("response_section", "")
        title = score.get("title", "")
        joined = section + title
        for module in modules:
            name = module["name"]
            if section in name or name in section:
                return module
            if "人员" in joined and "架构" in name:
                return module
            if "培训" in joined and "培训" in name:
                return module
            if "项目管理" in joined and "交付" in name:
                return module
        return modules[0] if modules else None

    @staticmethod
    def section_status(
        req_ids: list[str],
        score_ids: list[str],
        writing_ids: list[str],
        requirements: list[dict[str, Any]],
        scoring_items: list[dict[str, Any]],
        writing_items: list[dict[str, Any]],
        content_type: str,
    ) -> str:
        status_by_id = {item["requirement_id"]: item.get("status") for item in requirements}
        status_by_id.update({item["scoring_item_id"]: item.get("status") for item in scoring_items})
        status_by_id.update({item["writing_requirement_id"]: item.get("status") for item in writing_items})
        statuses = {status_by_id.get(item_id) for item_id in req_ids + score_ids + writing_ids}
        if content_type == "confirm_placeholder" or "confirm_required" in statuses:
            return "confirm_required"
        if content_type == "review_text" or "review_required" in statuses:
            return "review_required"
        return "planned"

    @staticmethod
    def coverage_status(status: str, refs: list[dict[str, str]]) -> str:
        if not refs:
            return "uncovered"
        if status == "confirm_required":
            return "confirm_required"
        if status == "review_required":
            return "review_required"
        return "planned"

    def validate_blueprint(self, blueprint: dict[str, Any], requirements: dict[str, Any]) -> None:
        required = [
            "schema_version",
            "artifact",
            "run_id",
            "generated_at",
            "producer",
            "inputs",
            "project_name",
            "architecture_layers",
            "modules",
            "sections",
            "diagram_plan",
            "coverage_map",
        ]
        missing = [field for field in required if field not in blueprint]
        if missing:
            raise RuntimeError(f"design-blueprint.json 缺少字段：{', '.join(missing)}")
        if blueprint["artifact"] != "design-blueprint" or blueprint["schema_version"] != SCHEMA_VERSION:
            raise RuntimeError("design-blueprint.json artifact 或 schema_version 不符合契约。")

        valid_req_ids = {item["requirement_id"] for item in requirements.get("requirements", [])}
        valid_req_ids.update(item["delivery_id"] for item in requirements.get("delivery_items", []))
        valid_score_ids = {item["scoring_item_id"] for item in requirements.get("scoring_items", [])}
        valid_writing_ids = {item["writing_requirement_id"] for item in requirements.get("writing_requirements", [])}

        self.ensure_unique("layer_id", blueprint["architecture_layers"])
        self.ensure_unique("module_id", blueprint["modules"])
        self.ensure_unique("section_id", blueprint["sections"])
        self.ensure_unique("diagram_id", blueprint["diagram_plan"])

        covered_ids = {item["source_id"] for item in blueprint["coverage_map"]}
        expected_ids = valid_req_ids | valid_score_ids | valid_writing_ids
        missing_coverage = sorted(expected_ids - covered_ids)
        if missing_coverage:
            raise RuntimeError(f"coverage_map 未覆盖来源 ID：{', '.join(missing_coverage[:20])}")

        for section in blueprint["sections"]:
            self.require_pattern(section["section_id"], r"^SEC[0-9]{3}$", "section_id")
            for req_id in section["source_requirement_ids"]:
                if req_id not in valid_req_ids:
                    raise RuntimeError(f"章节引用了不存在的需求 ID：{req_id}")
            for score_id in section.get("related_scoring_item_ids", []):
                if score_id not in valid_score_ids:
                    raise RuntimeError(f"章节引用了不存在的评分项 ID：{score_id}")
            for writing_id in section.get("writing_requirement_ids", []):
                if writing_id not in valid_writing_ids:
                    raise RuntimeError(f"章节引用了不存在的方案撰写要求 ID：{writing_id}")

        for diagram in blueprint["diagram_plan"]:
            if not diagram["source_requirement_ids"]:
                raise RuntimeError(f"图表 {diagram['diagram_id']} 缺少来源需求 ID。")
            for req_id in diagram["source_requirement_ids"]:
                if req_id not in valid_req_ids:
                    raise RuntimeError(f"图表引用了不存在的需求 ID：{req_id}")

    @staticmethod
    def ensure_unique(field: str, items: list[dict[str, Any]]) -> None:
        values = [item[field] for item in items]
        if len(values) != len(set(values)):
            raise RuntimeError(f"{field} 存在重复值。")

    @staticmethod
    def require_pattern(value: str, pattern: str, label: str) -> None:
        if not re.match(pattern, value):
            raise RuntimeError(f"{label} 格式不正确：{value}")

    def render_section_plan(self, blueprint: dict[str, Any]) -> str:
        lines = [
            "# Design Section Plan",
            "",
            f"- Run ID: `{blueprint['run_id']}`",
            f"- Generated At: `{blueprint['generated_at']}`",
            f"- Project: {blueprint['project_name']}",
            "",
            "## Architecture Layers",
            "",
            "| Layer | Name | Responsibility | Source IDs |",
            "|---|---|---|---|",
        ]
        for layer in blueprint["architecture_layers"]:
            lines.append(
                f"| {layer['layer_id']} | {self.escape_md(layer['name'])} | {self.escape_md(layer['responsibility'])} | {', '.join(layer['source_requirement_ids']) or '-'} |"
            )
        lines.extend(["", "## Modules", "", "| Module | Name | Layers | Source IDs | Scoring IDs | Diagrams |", "|---|---|---|---|---|---|"])
        for module in blueprint["modules"]:
            lines.append(
                f"| {module['module_id']} | {self.escape_md(module['name'])} | {', '.join(module.get('layer_ids', []))} | {', '.join(module['source_requirement_ids'])} | {', '.join(module.get('related_scoring_item_ids', [])) or '-'} | {', '.join(module.get('suggested_diagram_ids', [])) or '-'} |"
            )
        lines.extend(["", "## Sections", "", "| Section | Placeholder | Title | Type | Status | Source IDs | Scoring IDs | Writing IDs | Modules |", "|---|---|---|---|---|---|---|---|---|"])
        for section in blueprint["sections"]:
            lines.append(
                f"| {section['section_id']} | {self.escape_md(section['placeholder'])} | {self.escape_md(section['title'])} | {section['content_type']} | {section['status']} | {', '.join(section['source_requirement_ids']) or '-'} | {', '.join(section.get('related_scoring_item_ids', [])) or '-'} | {', '.join(section.get('writing_requirement_ids', [])) or '-'} | {', '.join(section.get('module_ids', [])) or '-'} |"
            )
        lines.append("")
        return "\n".join(lines)

    def render_diagram_plan(self, blueprint: dict[str, Any]) -> str:
        lines = [
            "# Diagram Plan",
            "",
            f"- Run ID: `{blueprint['run_id']}`",
            f"- Generated At: `{blueprint['generated_at']}`",
            "",
            "| Diagram | Title | Kind | Layout | Sections | Source IDs | Purpose |",
            "|---|---|---|---|---|---|---|",
        ]
        for diagram in blueprint["diagram_plan"]:
            lines.append(
                f"| {diagram['diagram_id']} | {self.escape_md(diagram['title'])} | {diagram['kind']} | {diagram.get('layout_hint', '-')} | {', '.join(diagram.get('related_section_ids', [])) or '-'} | {', '.join(diagram['source_requirement_ids'])} | {self.escape_md(diagram['purpose'])} |"
            )
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def unique(values: Any) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    def unique_valid_req_ids(self, values: Any) -> list[str]:
        return [value for value in self.unique(values) if isinstance(value, str) and REQ_ID_RE.match(value)]

    def unique_score_ids(self, values: Any) -> list[str]:
        return [value for value in self.unique(values) if isinstance(value, str) and SCORE_ID_RE.match(value)]

    def unique_writing_ids(self, values: Any) -> list[str]:
        return [value for value in self.unique(values) if isinstance(value, str) and WRITING_ID_RE.match(value)]

    @staticmethod
    def escape_md(value: str) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace.resolve())).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    def resolve_workspace_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.workspace / path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Design Agent.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root.")
    parser.add_argument("--records-dir", type=Path, default=Path("output/records"), help="Directory containing requirement artifacts.")
    parser.add_argument("--template-dir", type=Path, default=Path("templates"), help="Directory containing Word templates.")
    parser.add_argument("--output-dir", type=Path, default=Path("output/records"), help="Published record output directory.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    workspace = args.workspace.resolve()
    records_dir = args.records_dir if args.records_dir.is_absolute() else workspace / args.records_dir
    template_dir = args.template_dir if args.template_dir.is_absolute() else workspace / args.template_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else workspace / args.output_dir

    agent = DesignAgent(workspace=workspace, records_dir=records_dir, template_dir=template_dir, output_dir=output_dir)
    paths = agent.run()
    print(f"Design Agent completed: {agent.run_id}")
    print(f"staging: {paths['staging_dir']}")
    print(f"published: {paths['published_dir']}")
    print(f"output: {paths['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
