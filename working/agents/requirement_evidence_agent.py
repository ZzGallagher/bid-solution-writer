#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Requirement Evidence Agent.

This stage turns reviewed Markdown inputs into the pipeline fact source:
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

TECHNICAL_REQUIREMENTS_MD = "技术要求.md"
WRITING_REQUIREMENTS_MD = "方案撰写要求.md"
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


@dataclass(frozen=True)
class MarkdownItem:
    text: str
    locator: str
    heading_path: list[str]
    line_number: int


@dataclass(frozen=True)
class MarkdownContent:
    path: Path
    text: str
    items: list[MarkdownItem]
    headings: list[str]


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
        technical_path = self.resolve_markdown_input(TECHNICAL_REQUIREMENTS_MD, "*技术要求.md")
        writing_path = self.resolve_markdown_input(WRITING_REQUIREMENTS_MD, "*方案撰写要求.md")
        technical = self.read_markdown(technical_path)
        writing = self.read_markdown(writing_path)

        inputs = self.build_inputs(technical_path, writing_path)
        project = self.extract_project_from_markdown(technical)

        requirements: list[dict[str, Any]] = []
        requirements.extend(self.extract_markdown_technical_requirements(technical))

        writing_requirements = self.extract_writing_requirements(writing)
        delivery_items: list[dict[str, Any]] = []
        scoring_items: list[dict[str, Any]] = []

        requirements_json = {
            "schema_version": SCHEMA_VERSION,
            "artifact": "requirements",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": {"agent": AGENT_NAME, "version": AGENT_VERSION},
            "source_mode": "markdown_only",
            "source_documents": self.build_source_documents(technical_path, writing_path),
            "inputs": inputs,
            "project": project,
            "requirements": requirements,
            "writing_requirements": writing_requirements,
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

    def resolve_markdown_input(self, preferred_name: str, fallback_pattern: str) -> Path:
        preferred = self.input_dir / preferred_name
        if preferred.exists():
            return preferred
        matches = sorted(self.input_dir.glob(fallback_pattern))
        if len(matches) == 1:
            self.add_warning(
                f"未找到默认输入 {preferred_name}，已使用 {matches[0].name} 作为 Markdown 权威输入。",
                "info",
            )
            return matches[0]
        if not matches:
            raise FileNotFoundError(f"缺少 Markdown 权威输入文件：{preferred}")
        names = "、".join(path.name for path in matches)
        raise RuntimeError(f"存在多个可选 Markdown 输入匹配 {fallback_pattern}：{names}；请保留一个或重命名为 {preferred_name}。")

    def read_markdown(self, path: Path) -> MarkdownContent:
        if not path.exists():
            raise FileNotFoundError(f"缺少 Markdown 输入文件：{path}")
        text = path.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            raise RuntimeError(f"Markdown 输入为空：{path}")

        heading_stack: list[str] = []
        headings: list[str] = []
        items: list[MarkdownItem] = []
        for line_number, raw_line in enumerate(text.splitlines(), 1):
            line = raw_line.strip()
            if not line:
                continue
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                level = len(heading_match.group(1))
                heading = heading_match.group(2).strip()
                heading_stack = heading_stack[: level - 1]
                heading_stack.append(heading)
                headings.append(heading)
                continue
            normalized = self.markdown_item_text(line)
            if not normalized:
                continue
            locator = "/".join(heading_stack) if heading_stack else "正文"
            items.append(MarkdownItem(normalized, f"{locator}/L{line_number}", list(heading_stack), line_number))

        if not items:
            raise RuntimeError(f"Markdown 文件未抽取到可用条目：{path}")
        return MarkdownContent(path=path, text=text, items=items, headings=headings)

    @staticmethod
    def markdown_item_text(line: str) -> str:
        if re.match(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$", line):
            return ""
        if line.startswith("|") and line.endswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            return "；".join(cell for cell in cells if cell)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^[0-9]+[.)、]\s*", "", line)
        return re.sub(r"\s+", " ", line).strip()

    def build_source_documents(self, technical_path: Path, writing_path: Path) -> list[dict[str, str]]:
        return [
            {
                "document_id": "SRC001",
                "path": self.relative(technical_path),
                "kind": "technical_requirements_markdown",
                "sha256": self.sha256(technical_path),
            },
            {
                "document_id": "SRC002",
                "path": self.relative(writing_path),
                "kind": "writing_requirements_markdown",
                "sha256": self.sha256(writing_path),
            },
        ]

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

    def build_inputs(self, technical_path: Path, writing_path: Path) -> list[dict[str, Any]]:
        candidates = [
            (technical_path, "technical_requirements"),
            (writing_path, "writing_requirements"),
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

    def extract_project_from_markdown(self, technical: MarkdownContent) -> dict[str, Any]:
        project_name = technical.path.stem
        project_name = re.sub(r"(技术要求|需求规格|需求)$", "", project_name).strip("-_ 　") or project_name
        for heading in technical.headings:
            if re.match(r"^[0-9]+[.、）)]\s*", heading):
                continue
            if heading not in SECTION_HEADINGS and not heading.endswith("要求") and len(heading) >= 4:
                project_name = heading
                break

        return {
            "name": project_name,
            "tender_name": project_name,
            "bidder_name_status": "missing",
        }

    def extract_markdown_technical_requirements(self, document: MarkdownContent) -> list[dict[str, Any]]:
        requirements: list[dict[str, Any]] = []
        counters = {"T": 1, "P": 1, "Q": 1}
        for item in document.items:
            if self.is_low_value_line(item.text):
                continue
            section = item.heading_path[0] if item.heading_path else ""
            topic = item.heading_path[-1] if item.heading_path else section
            category, prefix, target_sections, need_diagram = self.classify_technical_line(section, topic, item.text)
            if not category:
                category, prefix = self.classify_markdown_fallback(item.heading_path, item.text)
                target_sections = self.target_sections_for_text(item.text, "功能设计" if prefix == "T" else "质量与安全设计")
                need_diagram = prefix == "T"

            req_id = f"{prefix}{counters[prefix]:03d}"
            counters[prefix] += 1
            title = self.title_from_text(topic if topic and topic != section else item.text, item.text)
            risk_level, status = self.risk_status(item.text)
            requirements.append(
                self.make_requirement(
                    req_id,
                    category,
                    title,
                    item.text,
                    document.path,
                    item.locator,
                    item.text,
                    self.keywords_from_text(" ".join(item.heading_path + [item.text])),
                    self.is_mandatory(item.text),
                    need_diagram,
                    risk_level,
                    target_sections,
                    status=status,
                )
            )

        if not requirements:
            self.add_warning("技术要求.md 未识别到技术需求条目。", "error")
        return requirements

    @staticmethod
    def classify_markdown_fallback(heading_path: list[str], text: str) -> tuple[str, str]:
        joined = " ".join(heading_path + [text])
        if any(keyword in joined for keyword in ("性能", "响应时间", "初始化", "查询时间", "稳定性", "MTTR")):
            return "technical_performance", "P"
        if any(keyword in joined for keyword in ("接口", "数据通信", "外部", "内部", "NMEA", "HMI")):
            return "technical_function", "T"
        if any(keyword in joined for keyword in ("功能", "支持", "具备", "显示", "规划", "告警", "处理")):
            return "technical_function", "T"
        return "technical_quality", "Q"

    def extract_writing_requirements(self, document: MarkdownContent) -> list[dict[str, Any]]:
        writing_requirements = []
        for index, item in enumerate(document.items, 1):
            title = self.title_from_text(item.heading_path[-1] if item.heading_path else item.text, item.text)
            target_sections, confidence = self.writing_target_sections(item.text, item.heading_path)
            writing_requirement_id = f"WR{index:03d}"
            if confidence == "low":
                self.add_confirm(
                    f"{title} 章节映射复核",
                    "方案撰写要求由系统低置信度自动映射，需复核是否进入正确章节。",
                    [writing_requirement_id],
                    "review_required",
                )
            writing_requirements.append(
                {
                    "writing_requirement_id": writing_requirement_id,
                    "title": title,
                    "text": item.text,
                    "source": self.source_ref(document.path, item.locator, item.text),
                    "keywords": self.keywords_from_text(" ".join(item.heading_path + [item.text])),
                    "target_sections": target_sections,
                    "mandatory_expansion": True,
                    "mapping_confidence": confidence,
                    "coverage_status": "planned",
                    "status": "review_required" if confidence == "low" else "extracted",
                }
            )
        return writing_requirements

    def writing_target_sections(self, text: str, heading_path: list[str]) -> tuple[list[str], str]:
        joined = " ".join(heading_path + [text])
        mapping = [
            (("需求分析", "全面性", "准确性", "充分性"), "需求分析"),
            (("总体", "业务", "逻辑", "技术", "数据架构", "架构"), "总体架构设计"),
            (("流程图", "业务流程", "数据处理流程"), "业务与数据流程设计"),
            (("接口", "数据交互"), "接口设计"),
            (("部署", "安装", "上线", "运行"), "部署方案"),
            (("安全", "网络安全", "保密"), "安全设计"),
            (("工具", "软件开发", "软件测试", "软件设计"), "开发测试工具"),
            (("关键技术", "创新", "可行性"), "关键技术"),
            (("实施周期", "进度", "人力资源", "过程管理"), "项目实施计划"),
            (("风险", "风险控制"), "质量控制与风险管理"),
            (("质量", "质量保证", "测试过程", "质量计划"), "质量保证与测试方案"),
            (("培训",), "培训与售后服务方案"),
            (("售后", "质保", "上门", "响应", "保修"), "培训与售后服务方案"),
            (("交付", "地点", "方式", "运输", "调试"), "项目实施与交付验收"),
            (("知识产权", "保密要求"), "安全与保密方案"),
        ]
        sections = [section for keywords, section in mapping if any(keyword in joined for keyword in keywords)]
        sections = list(dict.fromkeys(sections))
        if sections:
            return sections, "high"
        return ["方案撰写要求专项响应"], "low"

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

        for writing_item in requirements_json.get("writing_requirements", []):
            rows.append(
                {
                    "source_id": writing_item["writing_requirement_id"],
                    "source_type": "writing_requirement",
                    "title": writing_item["title"],
                    "target_sections": writing_item["target_sections"],
                    "planned_module_ids": [],
                    "planned_diagram_ids": [],
                    "coverage_status": writing_item.get("coverage_status", "planned"),
                    "notes": [
                        "必须扩写进入最终方案",
                        f"自动映射置信度：{writing_item.get('mapping_confidence', 'unknown')}",
                    ],
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
                "writing_requirements_total": len(requirements_json.get("writing_requirements", [])),
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
                "source_mode",
                "source_documents",
                "inputs",
                "project",
                "requirements",
                "writing_requirements",
                "scoring_items",
                "delivery_items",
                "confirm_candidates",
                "extraction_warnings",
            ],
            "requirements.json",
        )
        if artifact["source_mode"] != "markdown_only":
            raise RuntimeError("requirements.json source_mode 必须为 markdown_only。")
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
        writing_ids = [item["writing_requirement_id"] for item in artifact["writing_requirements"]]
        if len(writing_ids) != len(set(writing_ids)):
            raise RuntimeError("方案撰写要求 ID 重复。")
        for item in artifact["writing_requirements"]:
            self.require_fields(
                item,
                [
                    "writing_requirement_id",
                    "title",
                    "text",
                    "source",
                    "target_sections",
                    "mandatory_expansion",
                    "mapping_confidence",
                    "coverage_status",
                    "status",
                ],
                f"requirements.json#{item.get('writing_requirement_id', '?')}",
            )

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
            "writing_requirement": "方案撰写要求",
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
                f"- Writing requirements total: {summary.get('writing_requirements_total', 0)}",
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
