#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Requirement Evidence Agent.

This stage turns tender Word inputs into the pipeline fact source:
requirements.json, requirements-matrix.json/md, confirm-candidates.md, and
extraction-warnings.md.

The implementation intentionally uses only the Python standard library so the
agent can run in a fresh workspace without dependency installation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


AGENT_NAME = "Requirement Evidence Agent"
AGENT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"
WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

TECHNICAL_REQUIREMENTS = "技术要求.docx"
BUSINESS_REQUIREMENTS = "商务要求.docx"
SCORING_TABLE = "技术评分表.docx"
OPTIONAL_RULES = ("生成规则.md", "模板占位符说明.md")

SECTION_HEADINGS = {
    "执行的标准",
    "功能要求",
    "性能要求",
    "非功能性要求",
    "可靠性要求",
    "安全性要求",
    "保障性要求",
    "维修性要求",
    "测试性要求",
    "环境适应性要求",
    "接口需求",
    "内部接口",
    "外部接口",
    "设计约束",
    "质量监督和检验验收要求",
    "质量监督要求",
    "检验验收要求",
    "检验验收种类和方式",
    "拒收准则",
}

FUNCTION_TOPICS = {
    "海图综合态势展示",
    "AIS/雷达等目标信息的处理与显示",
    "航线规划",
    "安全等深线与水深告警",
    "航行监控导航与安全预警",
    "个性化设置需求",
    "基础平台与架构要求",
    "数据维护与合规",
}

HIGH_RISK_PATTERNS = {
    "人员": ("人员", "项目负责人", "团队", "社保", "驻场", "授课老师", "培训讲师"),
    "资质/证书": ("资质", "证书", "高级职称", "系统架构师", "软件设计师", "项目管理师"),
    "业绩": ("业绩", "销售合同", "验收报告", "银行收款"),
    "报价/付款": ("报价", "合同款", "付款", "保证金", "成本费", "金额"),
    "质保期": ("质保期", "保修", "升级服务期限"),
    "交付周期": ("交付", "进场", "安装调试", "上线运行", "实施周期"),
    "服务响应": ("7×24", "响应", "现场技术支持", "上门维护", "维修"),
    "驻场安排": ("驻场", "进驻", "甲方办公场所"),
    "承诺": ("承诺", "保证", "无效投标", "正偏离", "负偏离"),
}

GENERIC_RELATION_KEYWORDS = {
    "标准",
    "规范",
    "合规",
    "质量",
    "性能",
    "安全",
    "交付",
    "验收",
    "人员",
}


@dataclass(frozen=True)
class Paragraph:
    text: str
    locator: str


@dataclass(frozen=True)
class Table:
    rows: list[list[str]]
    locator: str


@dataclass
class DocxContent:
    path: Path
    paragraphs: list[Paragraph]
    tables: list[Table]


class RequirementEvidenceAgent:
    def __init__(self, workspace: Path, input_dir: Path, template_dir: Path, output_dir: Path) -> None:
        self.workspace = workspace
        self.input_dir = input_dir
        self.template_dir = template_dir
        self.output_dir = output_dir
        now = datetime.now().astimezone()
        self.generated_at = now.isoformat(timespec="seconds")
        self.run_id = f"RUN-{now:%Y%m%d-%H%M%S}"
        self.warning_index = 1
        self.confirm_index = 1
        self.warnings: list[dict[str, Any]] = []
        self.confirm_candidates: list[dict[str, Any]] = []

    def run(self) -> dict[str, Path]:
        technical = self.read_docx(self.input_dir / TECHNICAL_REQUIREMENTS)
        business = self.read_docx(self.input_dir / BUSINESS_REQUIREMENTS)
        scoring = self.read_docx(self.input_dir / SCORING_TABLE)

        inputs = self.build_inputs()
        project = self.extract_project(technical)

        requirements: list[dict[str, Any]] = []
        requirements.extend(self.extract_technical_requirements(technical))
        requirements.extend(self.extract_business_requirements(business))

        delivery_items = self.extract_delivery_items(technical)
        scoring_items = self.extract_scoring_items(scoring, requirements)
        self.add_project_confirm_candidates(scoring_items)
        self.link_scoring_items(requirements, scoring_items)

        requirements_json = {
            "schema_version": SCHEMA_VERSION,
            "artifact": "requirements",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": {"agent": AGENT_NAME, "version": AGENT_VERSION},
            "inputs": inputs,
            "project": project,
            "requirements": requirements,
            "scoring_items": scoring_items,
            "delivery_items": delivery_items,
            "confirm_candidates": self.confirm_candidates,
            "extraction_warnings": self.warnings,
        }

        matrix_json = self.build_matrix(requirements_json)
        self.validate_requirements(requirements_json)
        self.validate_matrix(matrix_json)

        staging_dir = self.workspace / "working" / "agent-system" / "staging" / "requirements" / self.run_id
        published_dir = self.workspace / "working" / "agent-system" / "published" / "requirements" / self.run_id
        for directory in (staging_dir, published_dir, self.output_dir):
            directory.mkdir(parents=True, exist_ok=True)

        artifacts = {
            "requirements.json": json.dumps(requirements_json, ensure_ascii=False, indent=2) + "\n",
            "requirements-matrix.json": json.dumps(matrix_json, ensure_ascii=False, indent=2) + "\n",
            "requirements-matrix.md": self.render_matrix_md(matrix_json),
            "confirm-candidates.md": self.render_confirm_candidates_md(requirements_json),
            "extraction-warnings.md": self.render_warnings_md(requirements_json),
        }

        for name, content in artifacts.items():
            (staging_dir / name).write_text(content, encoding="utf-8")

        for name in artifacts:
            shutil.copy2(staging_dir / name, published_dir / name)
            shutil.copy2(staging_dir / name, self.output_dir / name)

        return {
            "staging_dir": staging_dir,
            "published_dir": published_dir,
            "output_dir": self.output_dir,
        }

    def read_docx(self, path: Path) -> DocxContent:
        if not path.exists():
            raise FileNotFoundError(f"缺少核心输入文件：{path}")

        try:
            with zipfile.ZipFile(path) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
        except Exception as exc:  # noqa: BLE001 - convert parser errors to stage failure
            raise RuntimeError(f"无法读取 Word 文件：{path}") from exc

        body = root.find("w:body", WORD_NS)
        if body is None:
            raise RuntimeError(f"Word 文件缺少正文 XML：{path}")

        paragraphs: list[Paragraph] = []
        tables: list[Table] = []
        paragraph_index = 1
        table_index = 1
        for child in body:
            tag = self.local_name(child.tag)
            if tag == "p":
                text = self.text_from_node(child)
                if text:
                    paragraphs.append(Paragraph(text=text, locator=f"段落{paragraph_index}"))
                    paragraph_index += 1
            elif tag == "tbl":
                rows: list[list[str]] = []
                for tr in child.findall("./w:tr", WORD_NS):
                    cells = [self.text_from_node(tc) for tc in tr.findall("./w:tc", WORD_NS)]
                    if any(cells):
                        rows.append(cells)
                if rows:
                    tables.append(Table(rows=rows, locator=f"表{table_index}"))
                    table_index += 1

        if not paragraphs and not tables:
            raise RuntimeError(f"Word 文件未抽取到可用文本：{path}")

        return DocxContent(path=path, paragraphs=paragraphs, tables=tables)

    @staticmethod
    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    @staticmethod
    def text_from_node(node: ET.Element) -> str:
        parts = []
        for text_node in node.findall(".//w:t", WORD_NS):
            if text_node.text:
                parts.append(text_node.text)
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def build_inputs(self) -> list[dict[str, Any]]:
        candidates = [
            (self.input_dir / TECHNICAL_REQUIREMENTS, "technical_requirements"),
            (self.input_dir / BUSINESS_REQUIREMENTS, "business_requirements"),
            (self.input_dir / SCORING_TABLE, "scoring_table"),
            (self.template_dir / "投标方案模板.docx", "template"),
        ]
        for filename in OPTIONAL_RULES:
            candidates.append((self.template_dir / filename, "rules" if "规则" in filename else "placeholder_spec"))

        inputs = []
        for index, (path, kind) in enumerate(candidates, 1):
            if not path.exists():
                if kind in {"rules", "placeholder_spec"}:
                    self.add_warning(
                        f"可选模板辅助文件不存在：{self.relative(path)}，本次仅基于 Word 输入和内置规则抽取。",
                        "warning",
                    )
                continue
            inputs.append(
                {
                    "input_id": f"IN{index:03d}",
                    "path": self.relative(path),
                    "kind": kind,
                    "sha256": self.sha256(path),
                }
            )
        return inputs

    def extract_project(self, technical: DocxContent) -> dict[str, Any]:
        project_name = "待确认项目名称"
        for paragraph in technical.paragraphs:
            if paragraph.text and paragraph.text != "技术要求":
                project_name = paragraph.text
                break

        self.add_confirm("投标人名称", "输入资料未提供投标人名称。", [], "confirm_required")
        return {
            "name": project_name,
            "tender_name": project_name,
            "bidder_name_status": "confirm_required",
        }

    def extract_technical_requirements(self, document: DocxContent) -> list[dict[str, Any]]:
        requirements: list[dict[str, Any]] = []
        counters = {"T": 1, "P": 1, "Q": 1}
        section = ""
        topic = ""
        standards_buffer: list[Paragraph] = []

        for paragraph in document.paragraphs:
            text = paragraph.text
            if text in SECTION_HEADINGS:
                if section == "执行的标准" and standards_buffer:
                    requirements.append(
                        self.make_requirement(
                            f"Q{counters['Q']:03d}",
                            "technical_quality",
                            "执行标准符合性",
                            "；".join(item.text for item in standards_buffer),
                            document.path,
                            "执行的标准",
                            standards_buffer[0].text,
                            ["标准", "规范", "合规"],
                            True,
                            True,
                            "normal",
                            ["需求分析", "技术标准响应", "质量保证"],
                        )
                    )
                    counters["Q"] += 1
                    standards_buffer = []
                section = text
                topic = text
                continue

            if section == "执行的标准":
                if text != "本规格书执行下列法规、标准规范等。其最新版本适用于本规格书。":
                    standards_buffer.append(paragraph)
                continue

            if text in FUNCTION_TOPICS:
                topic = text
                continue

            if self.is_low_value_line(text):
                continue

            category, prefix, target_sections, need_diagram = self.classify_technical_line(section, topic, text)
            if not category:
                continue

            req_id = f"{prefix}{counters[prefix]:03d}"
            counters[prefix] += 1
            title = self.title_from_text(topic if topic and topic != section else text, text)
            risk_level, status = self.risk_status(text)
            requirement = self.make_requirement(
                req_id,
                category,
                title,
                text,
                document.path,
                f"{section}/{topic}/{paragraph.locator}",
                text,
                self.keywords_from_text(text),
                self.is_mandatory(text),
                need_diagram,
                risk_level,
                status=status,
                target_sections=target_sections,
            )
            requirements.append(requirement)

        if standards_buffer:
            requirements.append(
                self.make_requirement(
                    f"Q{counters['Q']:03d}",
                    "technical_quality",
                    "执行标准符合性",
                    "；".join(item.text for item in standards_buffer),
                    document.path,
                    "执行的标准",
                    standards_buffer[0].text,
                    ["标准", "规范", "合规"],
                    True,
                    True,
                    "normal",
                    ["需求分析", "技术标准响应", "质量保证"],
                )
            )

        return requirements

    def classify_technical_line(
        self, section: str, topic: str, text: str
    ) -> tuple[str | None, str, list[str], bool]:
        if section == "功能要求" or topic in FUNCTION_TOPICS:
            return "technical_function", "T", self.target_sections_for_text(text, "功能设计"), True
        if section == "性能要求":
            return "technical_performance", "P", ["性能设计", "技术指标响应"], False
        if section in {"接口需求", "内部接口", "外部接口"} or topic in {"内部接口", "外部接口"}:
            return "technical_function", "T", self.target_sections_for_text(text, "接口设计"), True
        if section in {
            "非功能性要求",
            "可靠性要求",
            "安全性要求",
            "保障性要求",
            "维修性要求",
            "测试性要求",
            "环境适应性要求",
            "设计约束",
            "质量监督和检验验收要求",
            "质量监督要求",
            "检验验收要求",
            "检验验收种类和方式",
            "拒收准则",
        }:
            return "technical_quality", "Q", self.target_sections_for_text(text, "质量与安全设计"), False
        return None, "T", [], False

    def extract_business_requirements(self, document: DocxContent) -> list[dict[str, Any]]:
        requirements: list[dict[str, Any]] = []
        counter = 1
        table = self.find_table_with_headers(document, {"序号", "参数性质", "类型", "要求"})
        if table is None:
            self.add_warning("商务要求未找到结构化表格，无法按行抽取商务条款。", "error")
            return requirements

        for row_index, row in enumerate(table.rows[1:], 2):
            if len(row) < 4:
                self.add_warning(f"商务要求{table.locator}第{row_index}行列数不足，已跳过。", "warning")
                continue
            nature, title, text = row[1].strip(), row[2].strip(), row[3].strip()
            if not title or not text:
                continue
            req_id = f"B{counter:03d}"
            counter += 1
            mandatory = "★" in nature or self.is_mandatory(text)
            risk_level, status = self.risk_status(text)
            requirement = self.make_requirement(
                req_id,
                "business",
                title,
                text,
                document.path,
                f"{table.locator}第{row_index}行/{title}",
                text,
                self.keywords_from_text(title + text),
                mandatory,
                False,
                "high" if mandatory else risk_level,
                status=status,
                target_sections=self.business_target_sections(title, text),
            )
            requirements.append(requirement)
        return requirements

    def extract_delivery_items(self, document: DocxContent) -> list[dict[str, Any]]:
        delivery_items: list[dict[str, Any]] = []
        table = self.find_table_with_headers(document, {"序号", "交付物名称", "数量", "说明"})
        if table is None:
            self.add_warning("技术要求未找到交付物表，delivery_items 为空。", "warning")
            return delivery_items

        for index, row in enumerate(table.rows[1:], 1):
            if len(row) < 4:
                self.add_warning(f"交付物{table.locator}第{index + 1}行列数不足，已跳过。", "warning")
                continue
            name, quantity, medium = row[1].strip(), row[2].strip(), row[3].strip()
            if not name:
                continue
            delivery_items.append(
                {
                    "delivery_id": f"D{index:03d}",
                    "name": name,
                    "quantity": quantity,
                    "medium": medium,
                    "source": self.source_ref(document.path, f"{table.locator}第{index + 1}行", name),
                    "status": "extracted",
                }
            )
        return delivery_items

    def extract_scoring_items(
        self, document: DocxContent, requirements: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        scoring_items: list[dict[str, Any]] = []
        table = self.find_table_with_headers(document, {"评审项", "详细描述", "分值"})
        if table is None:
            self.add_warning("技术评分表未找到结构化评分表，scoring_items 为空。", "error")
            return scoring_items

        current_category = ""
        for row_index, row in enumerate(table.rows[2:], 3):
            if len(row) < 6:
                continue
            if row[0].strip():
                current_category = row[0].strip()
            title = row[1].strip()
            text = row[2].strip()
            score_raw = row[3].strip()
            if not title or not text or not self.looks_like_score(score_raw):
                continue
            score_id = f"S{len(scoring_items) + 1:03d}"
            related = self.related_requirements_for_scoring(text, title, requirements)
            status = "review_required" if self.contains_high_risk(text) else "extracted"
            scoring_items.append(
                {
                    "scoring_item_id": score_id,
                    "title": title if title != current_category else title,
                    "text": text,
                    "source": self.source_ref(document.path, f"{table.locator}第{row_index}行/{title}", text),
                    "score": {"raw": score_raw, "value": float(score_raw.replace("分", ""))},
                    "response_section": self.response_section_for_scoring(title, text),
                    "related_requirement_ids": related,
                    "status": status,
                }
            )
        return scoring_items

    def add_project_confirm_candidates(self, scoring_items: list[dict[str, Any]]) -> None:
        for item in scoring_items:
            field = self.confirm_field_from_scoring(item["title"], item["text"])
            if not field:
                continue
            self.add_confirm(
                field,
                "评分项要求投标文件提供供应商自身证明材料，输入资料未提供该事实，后续正文不得自动补齐。",
                [item["scoring_item_id"]],
                "confirm_required",
            )

    def link_scoring_items(
        self, requirements: list[dict[str, Any]], scoring_items: list[dict[str, Any]]
    ) -> None:
        by_id = {item["requirement_id"]: item for item in requirements}
        for scoring_item in scoring_items:
            for requirement_id in scoring_item.get("related_requirement_ids", []):
                requirement = by_id.get(requirement_id)
                if not requirement:
                    continue
                linked = requirement.setdefault("related_scoring_item_ids", [])
                if scoring_item["scoring_item_id"] not in linked:
                    linked.append(scoring_item["scoring_item_id"])

    def build_matrix(self, requirements_json: dict[str, Any]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        module_index = 1
        diagram_index = 1
        for requirement in requirements_json["requirements"]:
            coverage_status = self.coverage_status(requirement["status"], requirement["target_sections"])
            planned_diagrams = []
            if requirement.get("need_diagram"):
                planned_diagrams.append(f"DG{diagram_index:03d}")
                diagram_index += 1
            rows.append(
                {
                    "source_id": requirement["requirement_id"],
                    "source_type": "requirement",
                    "title": requirement["title"],
                    "target_sections": requirement["target_sections"],
                    "planned_module_ids": [f"M{module_index:03d}"],
                    "planned_diagram_ids": planned_diagrams,
                    "coverage_status": coverage_status,
                    "notes": self.matrix_notes(requirement),
                }
            )
            module_index += 1

        for scoring_item in requirements_json["scoring_items"]:
            rows.append(
                {
                    "source_id": scoring_item["scoring_item_id"],
                    "source_type": "scoring_item",
                    "title": scoring_item["title"],
                    "target_sections": [scoring_item["response_section"]],
                    "planned_module_ids": [],
                    "planned_diagram_ids": [],
                    "coverage_status": self.coverage_status(scoring_item["status"], [scoring_item["response_section"]]),
                    "notes": [f"关联需求：{', '.join(scoring_item.get('related_requirement_ids', [])) or '待设计阶段细化'}"],
                }
            )

        for delivery_item in requirements_json["delivery_items"]:
            rows.append(
                {
                    "source_id": delivery_item["delivery_id"],
                    "source_type": "delivery_item",
                    "title": delivery_item["name"],
                    "target_sections": ["交付物清单", "项目交付方案"],
                    "planned_module_ids": [],
                    "planned_diagram_ids": [],
                    "coverage_status": "planned",
                    "notes": [f"数量：{delivery_item.get('quantity', '')}；介质：{delivery_item.get('medium', '')}"],
                }
            )

        uncovered_total = sum(1 for row in rows if row["coverage_status"] == "uncovered")
        return {
            "schema_version": SCHEMA_VERSION,
            "artifact": "requirements-matrix",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": {"agent": AGENT_NAME, "version": AGENT_VERSION},
            "inputs": [
                {
                    "artifact": "requirements",
                    "path": "output/records/requirements.json",
                    "schema_version": SCHEMA_VERSION,
                }
            ],
            "rows": rows,
            "summary": {
                "requirements_total": len(requirements_json["requirements"]),
                "scoring_items_total": len(requirements_json["scoring_items"]),
                "uncovered_total": uncovered_total,
            },
        }

    def make_requirement(
        self,
        requirement_id: str,
        category: str,
        title: str,
        text: str,
        source_file: Path,
        locator: str,
        quote: str,
        keywords: list[str],
        mandatory: bool,
        need_diagram: bool,
        risk_level: str,
        target_sections: list[str],
        status: str = "extracted",
    ) -> dict[str, Any]:
        requirement = {
            "requirement_id": requirement_id,
            "category": category,
            "title": title,
            "text": text,
            "source": self.source_ref(source_file, locator, quote),
            "keywords": keywords,
            "mandatory": mandatory,
            "need_diagram": need_diagram,
            "status": status,
            "risk_level": risk_level,
            "target_sections": target_sections,
            "warnings": [],
        }
        if status in {"review_required", "confirm_required"}:
            self.add_confirm(title, "该条目涉及高风险事实，后续响应应保持来源可追溯并进入复核。", [requirement_id], status)
        return requirement

    def source_ref(self, source_file: Path, locator: str, quote: str) -> dict[str, str]:
        return {
            "source_file": self.relative(source_file),
            "locator": locator,
            "quote": self.shorten(quote, 220),
            "normalized_text": re.sub(r"\s+", " ", quote).strip(),
        }

    def find_table_with_headers(self, document: DocxContent, headers: set[str]) -> Table | None:
        for table in document.tables:
            joined = set()
            for row in table.rows[:2]:
                joined.update(cell.strip() for cell in row if cell.strip())
            if headers.issubset(joined):
                return table
        return None

    def target_sections_for_text(self, text: str, default: str) -> list[str]:
        sections = [default]
        mapping = [
            (("架构", "跨平台", "模块化", "分层"), "总体架构设计"),
            (("接口", "NMEA", "数据通信", "外部设备"), "接口设计"),
            (("安全", "保密", "权限", "网络"), "安全设计"),
            (("部署", "运行环境", "国产化", "麒麟", "Ubuntu"), "部署方案"),
            (("测试", "验收", "质量", "可靠", "维修", "保障"), "质量保证与测试方案"),
            (("海图", "AIS", "雷达", "航线", "告警", "显示"), "功能设计"),
        ]
        for keywords, section in mapping:
            if any(keyword in text for keyword in keywords) and section not in sections:
                sections.append(section)
        return sections

    def business_target_sections(self, title: str, text: str) -> list[str]:
        joined = title + text
        if any(keyword in joined for keyword in ("交付", "进场", "安装", "调试")):
            return ["项目实施计划", "交付方案"]
        if any(keyword in joined for keyword in ("售后", "质保", "保修", "培训")):
            return ["培训与售后服务方案"]
        if any(keyword in joined for keyword in ("保密", "知识产权")):
            return ["安全与保密方案", "商务响应"]
        if any(keyword in joined for keyword in ("付款", "保证金", "报价")):
            return ["商务响应"]
        return ["商务响应"]

    def response_section_for_scoring(self, title: str, text: str) -> str:
        joined = title + text
        if "架构" in joined:
            return "总体架构设计"
        if "流程图" in joined or "数据处理流程" in joined:
            return "业务与数据流程设计"
        if "接口" in joined or "数据交互" in joined:
            return "接口设计"
        if "部署" in joined:
            return "部署方案"
        if "安全" in joined or "保密" in joined:
            return "安全设计"
        if "关键技术" in joined:
            return "关键技术"
        if "项目管理" in joined or "实施周期" in joined or "风险控制" in joined:
            return "项目管理和实施"
        if "质量" in joined:
            return "质量保证与测试方案"
        if "负责人" in joined or "团队" in joined or "人员" in joined:
            return "人员与资质证明"
        if "培训" in joined or "售后" in joined or "质保" in joined:
            return "培训与售后服务方案"
        if "技术指标" in joined:
            return "技术指标响应"
        return "技术方案"

    def related_requirements_for_scoring(
        self, text: str, title: str, requirements: list[dict[str, Any]]
    ) -> list[str]:
        section = self.response_section_for_scoring(title, text)
        related: list[str] = []
        for requirement in requirements:
            if section in requirement.get("target_sections", []):
                related.append(requirement["requirement_id"])
            else:
                distinctive_keywords = [
                    keyword
                    for keyword in requirement.get("keywords", [])
                    if keyword not in GENERIC_RELATION_KEYWORDS and len(keyword) >= 3
                ]
                if any(keyword in text for keyword in distinctive_keywords):
                    related.append(requirement["requirement_id"])
            if len(related) >= 8:
                break
        return related

    def confirm_field_from_scoring(self, title: str, text: str) -> str | None:
        joined = title + text
        if "项目负责人" in joined:
            return "项目负责人职称、证书、社保和业绩证明"
        if "团队" in joined or "人员" in joined:
            return "项目团队人员、证书和社保证明"
        if "工具" in joined and ("发票" in joined or "合作协议" in joined or "承诺书" in joined):
            return "软件开发、设计、建模、测试工具证明材料"
        if "质保期" in joined or "升级服务期限" in joined:
            return "质保期和升级服务期限响应口径"
        if "报价供应商服务方式" in joined:
            return "售后服务方式、现场支持和服务等级"
        return None

    def risk_status(self, text: str) -> tuple[str, str]:
        if not self.contains_high_risk(text):
            return "normal", "extracted"
        if any(keyword in text for keyword in ("投标供应商", "中标供应商", "乙方", "报价供应商")):
            return "high", "review_required"
        return "high", "extracted"

    def contains_high_risk(self, text: str) -> bool:
        return any(keyword in text for patterns in HIGH_RISK_PATTERNS.values() for keyword in patterns)

    def is_mandatory(self, text: str) -> bool:
        return any(keyword in text for keyword in ("必须", "须", "需", "不得", "应当", "★", "不低于", "不超过"))

    def keywords_from_text(self, text: str) -> list[str]:
        candidates = [
            "海图",
            "S-57",
            "S-63",
            "AIS",
            "雷达",
            "ARPA",
            "航线",
            "告警",
            "安全",
            "接口",
            "NMEA",
            "部署",
            "国产化",
            "性能",
            "质保",
            "交付",
            "培训",
            "保密",
            "验收",
            "质量",
            "人员",
        ]
        found = [keyword for keyword in candidates if keyword in text]
        if found:
            return found[:6]
        tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9./+-]*|[\u4e00-\u9fa5]{2,}", text)
        return list(dict.fromkeys(tokens[:4]))

    def title_from_text(self, topic: str, text: str) -> str:
        if topic and len(topic) <= 32 and topic not in {"功能要求", "性能要求", "非功能性要求"}:
            if "：" in text or len(text) > 42:
                suffix = text.split("：", 1)[0]
                if 2 <= len(suffix) <= 18 and suffix != topic:
                    return f"{topic}-{suffix}"
            return topic
        title = re.split(r"[，。；：:]", text, maxsplit=1)[0]
        return self.shorten(re.sub(r"^[0-9]+[）.)、]\s*", "", title), 32)

    def coverage_status(self, status: str, target_sections: list[str]) -> str:
        if not target_sections:
            return "uncovered"
        if status == "confirm_required":
            return "confirm_required"
        if status == "review_required":
            return "review_required"
        return "planned"

    def matrix_notes(self, requirement: dict[str, Any]) -> list[str]:
        notes = []
        if requirement.get("mandatory"):
            notes.append("强制/应答条款")
        if requirement.get("risk_level") in {"high", "critical"}:
            notes.append("高风险事实，后续正文需复核")
        if requirement.get("need_diagram"):
            notes.append("建议在设计阶段规划图表")
        return notes

    def looks_like_score(self, value: str) -> bool:
        return bool(re.match(r"^[0-9]+(?:\.[0-9]+)?(?:分)?$", value.strip()))

    def is_low_value_line(self, text: str) -> bool:
        if len(text) <= 1:
            return True
        if text in {"技术要求", "序号", "交付物名称", "数量", "说明", "表1 乙方需提供给甲方的交付物"}:
            return True
        return False

    def add_confirm(self, field: str, reason: str, source_ids: list[str], status: str) -> None:
        key = (field, tuple(source_ids), status)
        for item in self.confirm_candidates:
            if (item["field"], tuple(item.get("source_ids", [])), item["status"]) == key:
                return
        self.confirm_candidates.append(
            {
                "item_id": f"CF{self.confirm_index:03d}",
                "field": field,
                "reason": reason,
                "source_ids": source_ids,
                "status": status,
            }
        )
        self.confirm_index += 1

    def add_warning(
        self,
        message: str,
        severity: str,
        source: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        warning: dict[str, Any] = {
            "warning_id": f"EW{self.warning_index:03d}",
            "message": message,
            "severity": severity,
        }
        if source:
            warning["source"] = source
        self.warnings.append(warning)
        self.warning_index += 1
        return warning

    def validate_requirements(self, artifact: dict[str, Any]) -> None:
        self.require_fields(
            artifact,
            [
                "schema_version",
                "artifact",
                "run_id",
                "generated_at",
                "producer",
                "inputs",
                "project",
                "requirements",
                "scoring_items",
                "delivery_items",
                "confirm_candidates",
                "extraction_warnings",
            ],
            "requirements.json",
        )
        if not artifact["requirements"]:
            raise RuntimeError("需求抽取结果为空。")
        ids = [item["requirement_id"] for item in artifact["requirements"]]
        if len(ids) != len(set(ids)):
            raise RuntimeError("需求 ID 重复。")
        scoring_ids = [item["scoring_item_id"] for item in artifact["scoring_items"]]
        if len(scoring_ids) != len(set(scoring_ids)):
            raise RuntimeError("评分项 ID 重复。")
        for item in artifact["requirements"]:
            self.require_fields(
                item,
                ["requirement_id", "category", "title", "text", "source", "status", "risk_level", "target_sections"],
                f"requirements.json#{item.get('requirement_id', '?')}",
            )
            if not item["target_sections"] and item["status"] not in {"confirm_required", "review_required"}:
                raise RuntimeError(f"{item['requirement_id']} 缺少建议响应章节。")

    def validate_matrix(self, artifact: dict[str, Any]) -> None:
        self.require_fields(
            artifact,
            ["schema_version", "artifact", "run_id", "generated_at", "producer", "inputs", "rows", "summary"],
            "requirements-matrix.json",
        )
        for row in artifact["rows"]:
            self.require_fields(row, ["source_id", "source_type", "title", "target_sections", "coverage_status"], "matrix row")

    @staticmethod
    def require_fields(obj: dict[str, Any], fields: list[str], label: str) -> None:
        missing = [field for field in fields if field not in obj]
        if missing:
            raise RuntimeError(f"{label} 缺少字段：{', '.join(missing)}")

    def render_matrix_md(self, matrix: dict[str, Any]) -> str:
        lines = [
            "# Requirements Matrix",
            "",
            f"- Run ID: `{matrix['run_id']}`",
            f"- Generated At: `{matrix['generated_at']}`",
            f"- Producer: {AGENT_NAME} {AGENT_VERSION}",
            "",
            "| 来源ID | 类型 | 标题 | 建议响应章节 | 计划模块 | 计划图表 | 覆盖状态 | 备注 |",
            "|---|---|---|---|---|---|---|---|",
        ]
        type_names = {
            "requirement": "需求",
            "scoring_item": "评分项",
            "delivery_item": "交付物",
        }
        for row in matrix["rows"]:
            lines.append(
                "| {source_id} | {source_type} | {title} | {sections} | {modules} | {diagrams} | {status} | {notes} |".format(
                    source_id=row["source_id"],
                    source_type=type_names.get(row["source_type"], row["source_type"]),
                    title=self.escape_md(row["title"]),
                    sections="<br>".join(self.escape_md(item) for item in row["target_sections"]),
                    modules=", ".join(row.get("planned_module_ids", [])) or "-",
                    diagrams=", ".join(row.get("planned_diagram_ids", [])) or "-",
                    status=row["coverage_status"],
                    notes="<br>".join(self.escape_md(item) for item in row.get("notes", [])) or "-",
                )
            )
        summary = matrix["summary"]
        lines.extend(
            [
                "",
                "## Summary",
                "",
                f"- Requirements total: {summary['requirements_total']}",
                f"- Scoring items total: {summary['scoring_items_total']}",
                f"- Uncovered total: {summary['uncovered_total']}",
                "",
            ]
        )
        return "\n".join(lines)

    def render_confirm_candidates_md(self, artifact: dict[str, Any]) -> str:
        lines = [
            "# Confirm Candidates",
            "",
            f"- Run ID: `{artifact['run_id']}`",
            f"- Generated At: `{artifact['generated_at']}`",
            "",
        ]
        if not artifact["confirm_candidates"]:
            lines.extend(["本次未识别到需要人工确认或复核的候选项。", ""])
            return "\n".join(lines)
        lines.extend(["| ID | 字段 | 状态 | 来源 | 原因 |", "|---|---|---|---|---|"])
        for item in artifact["confirm_candidates"]:
            lines.append(
                "| {item_id} | {field} | {status} | {sources} | {reason} |".format(
                    item_id=item["item_id"],
                    field=self.escape_md(item["field"]),
                    status=item["status"],
                    sources=", ".join(item.get("source_ids", [])) or "-",
                    reason=self.escape_md(item["reason"]),
                )
            )
        lines.append("")
        return "\n".join(lines)

    def render_warnings_md(self, artifact: dict[str, Any]) -> str:
        lines = [
            "# Extraction Warnings",
            "",
            f"- Run ID: `{artifact['run_id']}`",
            f"- Generated At: `{artifact['generated_at']}`",
            "",
        ]
        if not artifact["extraction_warnings"]:
            lines.extend(["本次未记录抽取 warning。", ""])
            return "\n".join(lines)
        lines.extend(["| ID | 严重级别 | 来源 | 说明 |", "|---|---|---|---|"])
        for item in artifact["extraction_warnings"]:
            source = item.get("source", {})
            source_text = source.get("source_file", "-")
            if source.get("locator"):
                source_text += f"#{source['locator']}"
            lines.append(
                "| {warning_id} | {severity} | {source} | {message} |".format(
                    warning_id=item["warning_id"],
                    severity=item["severity"],
                    source=self.escape_md(source_text),
                    message=self.escape_md(item["message"]),
                )
            )
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def escape_md(value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")

    @staticmethod
    def shorten(value: str, max_length: int) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if len(value) <= max_length:
            return value
        return value[: max_length - 1] + "…"

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace.resolve())).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Requirement Evidence Agent.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root.")
    parser.add_argument("--input-dir", type=Path, default=Path("input"), help="Directory containing input docx files.")
    parser.add_argument("--template-dir", type=Path, default=Path("templates"), help="Directory containing templates.")
    parser.add_argument("--output-dir", type=Path, default=Path("output/records"), help="Published record output directory.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    workspace = args.workspace.resolve()
    input_dir = args.input_dir if args.input_dir.is_absolute() else workspace / args.input_dir
    template_dir = args.template_dir if args.template_dir.is_absolute() else workspace / args.template_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else workspace / args.output_dir

    agent = RequirementEvidenceAgent(
        workspace=workspace,
        input_dir=input_dir,
        template_dir=template_dir,
        output_dir=output_dir,
    )
    paths = agent.run()
    print(f"Requirement Evidence Agent completed: {agent.run_id}")
    print(f"staging: {paths['staging_dir']}")
    print(f"published: {paths['published_dir']}")
    print(f"output: {paths['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
