#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Word Layout Agent.

This final stage consumes Review Gate approved artifacts, copies the Word
template, fills placeholders, inserts paragraphs/tables/images, and emits the
assembly records. It intentionally uses only the Python standard library.
"""

from __future__ import annotations

import argparse
import copy
import json
import mimetypes
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


AGENT_NAME = "Word Layout Agent"
AGENT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

WORD_NS = {"w": W_NS, "r": R_NS}
PLACEHOLDER_RE = re.compile(r"【(?P<kind>GEN|COPY|REVIEW|CONFIRM):(?P<label>[^】]+)】")
RESIDUAL_RE = re.compile(r"【(?P<kind>GEN|COPY|REVIEW|CONFIRM):[^】]+】|TODO|TBD|\{\{[^}]+\}\}")

ALLOWED_RESIDUAL_KINDS = {"CONFIRM"}
REQ_CATEGORY_TITLES = {
    "technical_function": "技术要求-功能要求",
    "technical_performance": "技术要求-性能要求",
    "technical_quality": "技术要求-通用质量特性要求",
}


for prefix, uri in (
    ("w", W_NS),
    ("r", R_NS),
    ("wp", WP_NS),
    ("a", A_NS),
    ("pic", PIC_NS),
):
    ET.register_namespace(prefix, uri)


def qn(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


@dataclass
class JsonLoad:
    artifact: str
    path: Path
    data: dict[str, Any] | None = None
    error: str | None = None

    @property
    def schema_version(self) -> str:
        if self.data:
            return str(self.data.get("schema_version") or "unknown")
        return self.error or "missing"


class WordLayoutAgent:
    def __init__(
        self,
        workspace: Path,
        records_dir: Path,
        template_path: Path,
        output_dir: Path,
        records_output_dir: Path,
    ) -> None:
        self.workspace = workspace
        self.records_dir = records_dir
        self.template_path = template_path
        self.output_dir = output_dir
        self.records_output_dir = records_output_dir
        now = datetime.now().astimezone()
        self.generated_at = now.isoformat(timespec="seconds")
        self.output_date = f"{now:%Y%m%d}"
        self.run_id = f"RUN-{now:%Y%m%d-%H%M%S}"
        self.placeholders: list[dict[str, Any]] = []
        self.events: list[str] = []
        self.failures: list[str] = []
        self.residuals: list[dict[str, str]] = []
        self.image_counter = 1
        self.media_files: dict[str, bytes] = {}

    def run(self) -> dict[str, Any]:
        artifacts = self.load_artifacts()
        self.run_id = self.resolve_run_id(artifacts)
        staging_dir = self.workspace / "working" / "agent-system" / "staging" / "assembly" / self.run_id
        published_dir = self.workspace / "working" / "agent-system" / "published" / "assembly" / self.run_id
        for directory in (staging_dir, published_dir, self.records_output_dir, self.output_dir):
            directory.mkdir(parents=True, exist_ok=True)

        project_name = self.project_name(artifacts.get("requirements", JsonLoad("requirements", Path())).data or {})
        output_docx = self.output_dir / f"{self.safe_filename(project_name)}设计方案_V1.00_{self.output_date}.docx"
        staging_docx = staging_dir / output_docx.name
        published_docx = published_dir / output_docx.name

        release = artifacts.get("release-decision", JsonLoad("release-decision", Path())).data or {}
        review_decision = str(release.get("decision") or "blocked")
        allow_word_assembly = bool(release.get("allow_word_assembly"))
        self.events.append(f"Review Gate decision={review_decision}, allow_word_assembly={allow_word_assembly}.")

        if review_decision != "approved" or not allow_word_assembly:
            self.add_placeholder(
                "Review Gate",
                "skipped",
                "release-decision.json",
                "Review Gate 未放行，未生成最终 Word。",
            )
            manifest = self.build_manifest(artifacts, output_docx, review_decision="blocked", assembly_status="blocked")
            self.publish_records(staging_dir, published_dir, manifest)
            return {
                "status": "blocked",
                "staging_dir": staging_dir,
                "published_dir": published_dir,
                "records_output_dir": self.records_output_dir,
                "output_docx": None,
            }

        self.check_required_inputs(artifacts)
        if not self.template_path.exists():
            self.failures.append(f"模板缺失：{self.relative(self.template_path)}。")

        if self.failures:
            manifest = self.build_manifest(artifacts, output_docx, review_decision="approved", assembly_status="failed")
            self.publish_records(staging_dir, published_dir, manifest)
            return {
                "status": "failed",
                "staging_dir": staging_dir,
                "published_dir": published_dir,
                "records_output_dir": self.records_output_dir,
                "output_docx": None,
            }

        try:
            self.assemble_docx(
                artifacts=artifacts,
                target_docx=staging_docx,
            )
        except Exception as exc:  # noqa: BLE001 - convert Word package errors to assembly records
            self.failures.append(f"Word 装配失败：{exc}")

        abnormal_residuals = [
            item for item in self.residuals if item["status"] != "allowed_confirm"
        ]
        if abnormal_residuals:
            self.failures.append("最终 Word 中存在未解释的异常占位符残留。")

        if self.failures:
            manifest = self.build_manifest(artifacts, output_docx, review_decision="approved", assembly_status="failed")
            self.publish_records(staging_dir, published_dir, manifest)
            return {
                "status": "failed",
                "staging_dir": staging_dir,
                "published_dir": published_dir,
                "records_output_dir": self.records_output_dir,
                "output_docx": None,
            }

        shutil.copy2(staging_docx, published_docx)
        shutil.copy2(staging_docx, output_docx)
        manifest = self.build_manifest(artifacts, output_docx, review_decision="approved", assembly_status="generated")
        self.publish_records(staging_dir, published_dir, manifest)
        return {
            "status": "generated",
            "staging_dir": staging_dir,
            "published_dir": published_dir,
            "records_output_dir": self.records_output_dir,
            "output_docx": output_docx,
        }

    def load_artifacts(self) -> dict[str, JsonLoad]:
        expected = {
            "requirements": "requirements.json",
            "content-blocks": "content-blocks.json",
            "diagram-manifest": "diagram-manifest.json",
            "release-decision": "release-decision.json",
        }
        loaded: dict[str, JsonLoad] = {}
        for artifact, filename in expected.items():
            path = self.records_dir / filename
            if not path.exists():
                loaded[artifact] = JsonLoad(artifact, path, error="missing")
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - record invalid upstream JSON
                loaded[artifact] = JsonLoad(artifact, path, error=f"invalid_json: {exc}")
                continue
            loaded[artifact] = JsonLoad(artifact, path, data=data)
        return loaded

    def resolve_run_id(self, artifacts: dict[str, JsonLoad]) -> str:
        for key in ("release-decision", "content-blocks", "requirements", "diagram-manifest"):
            data = artifacts.get(key, JsonLoad(key, Path())).data
            if data and data.get("run_id"):
                return str(data["run_id"])
        return self.run_id

    def check_required_inputs(self, artifacts: dict[str, JsonLoad]) -> None:
        expected_artifacts = {
            "requirements": "requirements",
            "content-blocks": "content-blocks",
            "diagram-manifest": "diagram-manifest",
            "release-decision": "release-decision",
        }
        for key, expected_artifact in expected_artifacts.items():
            artifact = artifacts[key]
            if artifact.error:
                self.failures.append(f"缺少或无法读取必需输入：{artifact.path.name}（{artifact.error}）。")
                continue
            if not artifact.data:
                self.failures.append(f"必需输入为空：{artifact.path.name}。")
                continue
            if artifact.data.get("schema_version") != SCHEMA_VERSION or artifact.data.get("artifact") != expected_artifact:
                self.failures.append(f"{artifact.path.name} 的 schema_version 或 artifact 字段不符合契约。")

    def assemble_docx(self, artifacts: dict[str, JsonLoad], target_docx: Path) -> None:
        requirements = artifacts["requirements"].data or {}
        content_blocks = artifacts["content-blocks"].data or {}
        diagram_manifest = artifacts["diagram-manifest"].data or {}
        block_index = {block["placeholder"]: block for block in content_blocks.get("blocks", []) if block.get("placeholder")}
        diagram_index = {
            diagram["diagram_id"]: diagram
            for diagram in diagram_manifest.get("diagrams", [])
            if diagram.get("diagram_id")
        }

        with zipfile.ZipFile(self.template_path, "r") as archive:
            package_files = {name: archive.read(name) for name in archive.namelist()}

        document_xml = package_files.get("word/document.xml")
        if document_xml is None:
            raise RuntimeError("模板缺少 word/document.xml。")
        root = ET.fromstring(document_xml)
        rels_root = self.load_relationships(package_files)
        content_types_root = self.load_content_types(package_files)

        placeholders_seen: set[str] = set()
        parent_map = {child: parent for parent in root.iter() for child in parent}
        for paragraph in list(root.findall(".//w:p", WORD_NS)):
            text = self.paragraph_text(paragraph)
            matches = list(PLACEHOLDER_RE.finditer(text))
            if not matches:
                continue
            if len(matches) > 1 or text.strip() != matches[0].group(0):
                new_text = text
                changed = False
                for match in matches:
                    placeholder = match.group(0)
                    kind = match.group("kind")
                    placeholders_seen.add(placeholder)
                    if kind == "COPY":
                        replacement = self.copy_replacement(match.group("label"), requirements, inline=True)
                        new_text = new_text.replace(placeholder, replacement)
                        self.add_placeholder(placeholder, "filled", "requirements.json", "已按需求事实源替换 COPY 占位符。")
                        changed = True
                    elif kind == "CONFIRM":
                        self.add_placeholder(placeholder, "preserved_confirm", self.confirm_source(placeholder, content_blocks), "已按规则保留 CONFIRM 占位符。")
                    else:
                        self.add_placeholder(placeholder, "failed", "template", "非独立段落中的 GEN/REVIEW 占位符无法安全装配。")
                        self.failures.append(f"非独立段落中的占位符无法装配：{placeholder}。")
                if changed:
                    self.replace_paragraph_text(paragraph, new_text)
                continue

            match = matches[0]
            placeholder = match.group(0)
            kind = match.group("kind")
            label = match.group("label")
            placeholders_seen.add(placeholder)
            parent = parent_map.get(paragraph)
            if parent is None:
                continue
            index = list(parent).index(paragraph)

            if kind == "CONFIRM":
                self.add_placeholder(placeholder, "preserved_confirm", self.confirm_source(placeholder, content_blocks), "已按规则保留 CONFIRM 占位符。")
                continue

            if kind == "COPY":
                replacements = self.copy_replacement_lines(label, requirements)
                elements = [self.make_paragraph(line, paragraph) for line in replacements]
                self.replace_element(parent, index, paragraph, elements)
                self.add_placeholder(placeholder, "filled", "requirements.json", "已按需求事实源替换 COPY 占位符。")
                continue

            block = block_index.get(placeholder)
            if not block:
                self.add_placeholder(placeholder, "missing", "content-blocks.json", "模板占位符没有对应内容块，已保留原占位符。")
                continue

            elements = self.elements_for_block(block, diagram_index, paragraph, rels_root, content_types_root)
            if not elements:
                self.add_placeholder(placeholder, "failed", block["block_id"], "内容块未生成可插入内容。")
                self.failures.append(f"内容块未生成可插入内容：{block['block_id']}。")
                continue
            self.replace_element(parent, index, paragraph, elements)
            status = "review_inserted" if block.get("status") == "review_required" or kind == "REVIEW" else "filled"
            self.add_placeholder(placeholder, status, block["block_id"], f"已插入 {block.get('type')} 内容块。")

        for block in content_blocks.get("blocks", []):
            placeholder = block.get("placeholder")
            if placeholder and placeholder not in placeholders_seen:
                self.add_placeholder(placeholder, "missing", block.get("block_id", "content-blocks.json"), "内容块占位符未在模板中找到。")

        self.scan_residuals(root)
        package_files["word/document.xml"] = self.xml_bytes(root)
        package_files["word/_rels/document.xml.rels"] = self.xml_bytes(rels_root)
        package_files["[Content_Types].xml"] = self.xml_bytes(content_types_root)
        for name, data in self.media_files.items():
            package_files[name] = data

        with zipfile.ZipFile(target_docx, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in package_files.items():
                archive.writestr(name, data)

    def elements_for_block(
        self,
        block: dict[str, Any],
        diagram_index: dict[str, dict[str, Any]],
        paragraph_template: ET.Element,
        rels_root: ET.Element,
        content_types_root: ET.Element,
    ) -> list[ET.Element]:
        block_type = block.get("type")
        content = block.get("content")
        if block_type == "rich_content" and isinstance(content, list):
            elements: list[ET.Element] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "diagram":
                    elements.extend(self.diagram_elements(item, diagram_index, paragraph_template, rels_root, content_types_root))
                elif isinstance(item, dict):
                    text = str(item.get("text") or item.get("caption") or "").strip()
                    if text:
                        elements.append(self.make_paragraph(text, paragraph_template))
                elif str(item).strip():
                    elements.append(self.make_paragraph(str(item), paragraph_template))
            return elements
        if block_type in {"paragraphs", "review_text", "list", "heading"} and isinstance(content, list):
            return [self.make_paragraph(str(item), paragraph_template) for item in content if str(item).strip()]
        if block_type == "table" and isinstance(content, dict):
            return [self.make_table(content)]
        if block_type == "confirm_placeholder" and isinstance(content, dict):
            return [self.make_paragraph(str(content.get("placeholder_text") or block.get("placeholder")), paragraph_template)]
        if block_type == "diagram_reference" and isinstance(content, dict):
            return self.diagram_elements(content, diagram_index, paragraph_template, rels_root, content_types_root)
        return []

    def diagram_elements(
        self,
        content: dict[str, Any],
        diagram_index: dict[str, dict[str, Any]],
        paragraph_template: ET.Element,
        rels_root: ET.Element,
        content_types_root: ET.Element,
    ) -> list[ET.Element]:
        diagram_id = str(content.get("diagram_id") or "")
        caption = str(content.get("caption") or "")
        diagram = diagram_index.get(diagram_id)
        if not diagram:
            self.failures.append(f"内容块引用的图表不存在于 diagram-manifest.json：{diagram_id}。")
            return []
        image_path = self.resolve_workspace_path(str(diagram.get("image_path") or ""))
        if not diagram.get("assembly_allowed"):
            self.failures.append(f"图表未允许进入 Word 装配：{diagram_id}。")
            return []
        if not image_path.exists():
            self.failures.append(f"图表图片路径无效：{diagram_id} -> {self.relative(image_path)}。")
            return []
        relationship_id = self.add_image_relationship(image_path, rels_root, content_types_root)
        width, height = self.image_dimensions(image_path)
        return [
            self.make_image_paragraph(relationship_id, diagram.get("title") or diagram_id, width, height),
            self.make_paragraph(caption, paragraph_template),
        ]

    def copy_replacement(self, label: str, requirements: dict[str, Any], inline: bool = False) -> str:
        lines = self.copy_replacement_lines(label, requirements)
        if inline:
            return "；".join(lines) if lines else f"【COPY:{label}】"
        return "\n".join(lines) if lines else f"【COPY:{label}】"

    def copy_replacement_lines(self, label: str, requirements: dict[str, Any]) -> list[str]:
        project = requirements.get("project") or {}
        if label == "项目名称":
            return [str(project.get("name") or project.get("tender_name") or "【CONFIRM:项目名称】")]

        category_by_title = {title: category for category, title in REQ_CATEGORY_TITLES.items()}
        category = category_by_title.get(label)
        if category:
            lines = []
            for item in requirements.get("requirements", []):
                if item.get("category") != category:
                    continue
                req_id = item.get("requirement_id", "")
                text = item.get("source", {}).get("quote") or item.get("text") or item.get("title") or ""
                lines.append(f"{req_id} {self.shorten(text, 180)}")
            return lines or [f"【COPY:{label}】"]

        if label == "交付物清单":
            lines = []
            for item in requirements.get("delivery_items", []):
                delivery_id = item.get("delivery_id", "")
                name = item.get("name", "")
                quantity = item.get("quantity", "")
                medium = item.get("medium", "")
                lines.append(f"{delivery_id} {name}；数量：{quantity or '按招标文件'}；介质：{medium or '按招标文件'}")
            return lines or [f"【COPY:{label}】"]

        return [f"【COPY:{label}】"]

    def add_image_relationship(self, image_path: Path, rels_root: ET.Element, content_types_root: ET.Element) -> str:
        relationship_id = self.next_relationship_id(rels_root)
        suffix = image_path.suffix.lower() or ".png"
        media_name = f"image{self.image_counter}{suffix}"
        self.image_counter += 1
        target = f"media/{media_name}"
        relationship = ET.Element(qn(REL_NS, "Relationship"))
        relationship.set("Id", relationship_id)
        relationship.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image")
        relationship.set("Target", target)
        rels_root.append(relationship)
        self.media_files[f"word/{target}"] = image_path.read_bytes()
        self.ensure_content_type(content_types_root, suffix)
        self.events.append(f"Inserted image {self.relative(image_path)} as word/{target}.")
        return relationship_id

    def next_relationship_id(self, rels_root: ET.Element) -> str:
        max_id = 0
        for relationship in rels_root.findall(qn(REL_NS, "Relationship")):
            rid = relationship.get("Id", "")
            if rid.startswith("rId") and rid[3:].isdigit():
                max_id = max(max_id, int(rid[3:]))
        return f"rId{max_id + 1}"

    def ensure_content_type(self, root: ET.Element, suffix: str) -> None:
        extension = suffix.lstrip(".").lower()
        if not extension:
            return
        for item in root.findall(qn(CT_NS, "Default")):
            if item.get("Extension", "").lower() == extension:
                return
        default = ET.Element(qn(CT_NS, "Default"))
        default.set("Extension", extension)
        default.set("ContentType", mimetypes.types_map.get(f".{extension}", "application/octet-stream"))
        root.append(default)

    def load_relationships(self, package_files: dict[str, bytes]) -> ET.Element:
        rels_xml = package_files.get("word/_rels/document.xml.rels")
        if rels_xml:
            return ET.fromstring(rels_xml)
        return ET.Element(qn(REL_NS, "Relationships"))

    def load_content_types(self, package_files: dict[str, bytes]) -> ET.Element:
        content_types_xml = package_files.get("[Content_Types].xml")
        if content_types_xml:
            return ET.fromstring(content_types_xml)
        return ET.Element(qn(CT_NS, "Types"))

    def make_paragraph(self, text: str, template: ET.Element | None = None) -> ET.Element:
        paragraph = ET.Element(qn(W_NS, "p"))
        if template is not None:
            ppr = template.find("w:pPr", WORD_NS)
            if ppr is not None:
                paragraph.append(copy.deepcopy(ppr))
        run = ET.SubElement(paragraph, qn(W_NS, "r"))
        text_node = ET.SubElement(run, qn(W_NS, "t"))
        text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text_node.text = text
        return paragraph

    def make_table(self, content: dict[str, Any]) -> ET.Element:
        columns = [str(item) for item in content.get("columns", [])]
        rows = [[str(cell) for cell in row] for row in content.get("rows", [])]
        table = ET.Element(qn(W_NS, "tbl"))
        tbl_pr = ET.SubElement(table, qn(W_NS, "tblPr"))
        borders = ET.SubElement(tbl_pr, qn(W_NS, "tblBorders"))
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            border = ET.SubElement(borders, qn(W_NS, side))
            border.set(qn(W_NS, "val"), "single")
            border.set(qn(W_NS, "sz"), "4")
            border.set(qn(W_NS, "space"), "0")
            border.set(qn(W_NS, "color"), "auto")
        for row in ([columns] if columns else []) + rows:
            tr = ET.SubElement(table, qn(W_NS, "tr"))
            for cell in row:
                tc = ET.SubElement(tr, qn(W_NS, "tc"))
                tc_pr = ET.SubElement(tc, qn(W_NS, "tcPr"))
                width = ET.SubElement(tc_pr, qn(W_NS, "tcW"))
                width.set(qn(W_NS, "w"), "2400")
                width.set(qn(W_NS, "type"), "dxa")
                tc.append(self.make_paragraph(cell))
        return table

    def make_image_paragraph(self, relationship_id: str, title: str, width_px: int, height_px: int) -> ET.Element:
        max_cx = 5486400
        width_px = width_px or 1200
        height_px = height_px or 800
        ratio = height_px / width_px if width_px else 0.67
        cx = max_cx
        cy = int(cx * ratio)
        doc_pr_id = self.image_counter + 1000

        paragraph = ET.Element(qn(W_NS, "p"))
        run = ET.SubElement(paragraph, qn(W_NS, "r"))
        drawing = ET.SubElement(run, qn(W_NS, "drawing"))
        inline = ET.SubElement(drawing, qn(WP_NS, "inline"))
        for key in ("distT", "distB", "distL", "distR"):
            inline.set(key, "0")
        extent = ET.SubElement(inline, qn(WP_NS, "extent"))
        extent.set("cx", str(cx))
        extent.set("cy", str(cy))
        effect_extent = ET.SubElement(inline, qn(WP_NS, "effectExtent"))
        for key in ("l", "t", "r", "b"):
            effect_extent.set(key, "0")
        doc_pr = ET.SubElement(inline, qn(WP_NS, "docPr"))
        doc_pr.set("id", str(doc_pr_id))
        doc_pr.set("name", str(title))
        cnv = ET.SubElement(inline, qn(WP_NS, "cNvGraphicFramePr"))
        locks = ET.SubElement(cnv, qn(A_NS, "graphicFrameLocks"))
        locks.set("noChangeAspect", "1")
        graphic = ET.SubElement(inline, qn(A_NS, "graphic"))
        graphic_data = ET.SubElement(graphic, qn(A_NS, "graphicData"))
        graphic_data.set("uri", "http://schemas.openxmlformats.org/drawingml/2006/picture")
        pic = ET.SubElement(graphic_data, qn(PIC_NS, "pic"))
        nv_pic_pr = ET.SubElement(pic, qn(PIC_NS, "nvPicPr"))
        c_nv_pr = ET.SubElement(nv_pic_pr, qn(PIC_NS, "cNvPr"))
        c_nv_pr.set("id", "0")
        c_nv_pr.set("name", str(title))
        ET.SubElement(nv_pic_pr, qn(PIC_NS, "cNvPicPr"))
        blip_fill = ET.SubElement(pic, qn(PIC_NS, "blipFill"))
        blip = ET.SubElement(blip_fill, qn(A_NS, "blip"))
        blip.set(qn(R_NS, "embed"), relationship_id)
        stretch = ET.SubElement(blip_fill, qn(A_NS, "stretch"))
        ET.SubElement(stretch, qn(A_NS, "fillRect"))
        sp_pr = ET.SubElement(pic, qn(PIC_NS, "spPr"))
        xfrm = ET.SubElement(sp_pr, qn(A_NS, "xfrm"))
        off = ET.SubElement(xfrm, qn(A_NS, "off"))
        off.set("x", "0")
        off.set("y", "0")
        ext = ET.SubElement(xfrm, qn(A_NS, "ext"))
        ext.set("cx", str(cx))
        ext.set("cy", str(cy))
        prst_geom = ET.SubElement(sp_pr, qn(A_NS, "prstGeom"))
        prst_geom.set("prst", "rect")
        ET.SubElement(prst_geom, qn(A_NS, "avLst"))
        return paragraph

    def replace_paragraph_text(self, paragraph: ET.Element, text: str) -> None:
        ppr = paragraph.find("w:pPr", WORD_NS)
        for child in list(paragraph):
            paragraph.remove(child)
        if ppr is not None:
            paragraph.append(ppr)
        run = ET.SubElement(paragraph, qn(W_NS, "r"))
        text_node = ET.SubElement(run, qn(W_NS, "t"))
        text_node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        text_node.text = text

    @staticmethod
    def replace_element(parent: ET.Element, index: int, old: ET.Element, replacements: list[ET.Element]) -> None:
        for offset, replacement in enumerate(replacements):
            parent.insert(index + offset, replacement)
        parent.remove(old)

    @staticmethod
    def paragraph_text(paragraph: ET.Element) -> str:
        return "".join(node.text or "" for node in paragraph.findall(".//w:t", WORD_NS))

    def scan_residuals(self, root: ET.Element) -> None:
        self.residuals = []
        for paragraph in root.findall(".//w:p", WORD_NS):
            text = self.paragraph_text(paragraph)
            for match in RESIDUAL_RE.finditer(text):
                token = match.group(0)
                kind_match = PLACEHOLDER_RE.match(token)
                kind = kind_match.group("kind") if kind_match else "raw"
                status = "allowed_confirm" if kind in ALLOWED_RESIDUAL_KINDS else "unresolved"
                self.residuals.append({"placeholder": token, "kind": kind, "status": status})

    @staticmethod
    def image_dimensions(path: Path) -> tuple[int, int]:
        data = path.read_bytes()[:32]
        if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        return 1200, 800

    def publish_records(self, staging_dir: Path, published_dir: Path, manifest: dict[str, Any]) -> None:
        outputs = {
            "assembly-manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            "placeholder-fill-log.md": self.render_placeholder_log(),
            "assembly-log.md": self.render_assembly_log(manifest),
            "residual-placeholder-check.md": self.render_residual_check(),
        }
        for name, content in outputs.items():
            (staging_dir / name).write_text(content, encoding="utf-8")
        for name in outputs:
            shutil.copy2(staging_dir / name, published_dir / name)
            shutil.copy2(staging_dir / name, self.records_output_dir / name)

    def build_manifest(
        self,
        artifacts: dict[str, JsonLoad],
        output_docx: Path,
        review_decision: str,
        assembly_status: str,
    ) -> dict[str, Any]:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "artifact": "assembly-manifest",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": {"agent": AGENT_NAME, "version": AGENT_VERSION},
            "inputs": self.build_inputs(artifacts),
            "template_path": self.relative(self.template_path),
            "output_docx": self.relative(output_docx),
            "review_decision": "approved" if review_decision == "approved" else "blocked",
            "placeholders": self.placeholders,
            "assembly_status": assembly_status,
            "logs": [
                {"kind": "placeholder-fill-log", "path": self.relative(self.records_output_dir / "placeholder-fill-log.md")},
                {"kind": "assembly-log", "path": self.relative(self.records_output_dir / "assembly-log.md")},
                {"kind": "residual-placeholder-check", "path": self.relative(self.records_output_dir / "residual-placeholder-check.md")},
            ],
        }
        self.validate_manifest(manifest)
        return manifest

    def build_inputs(self, artifacts: dict[str, JsonLoad]) -> list[dict[str, str]]:
        inputs = [
            {"artifact": "template", "path": self.relative(self.template_path), "schema_version": "template-docx"},
        ]
        for key in ("content-blocks", "diagram-manifest", "requirements", "release-decision"):
            artifact = artifacts[key]
            inputs.append(
                {
                    "artifact": key,
                    "path": self.relative(artifact.path),
                    "schema_version": artifact.schema_version,
                }
            )
        return inputs

    def validate_manifest(self, manifest: dict[str, Any]) -> None:
        required = [
            "schema_version",
            "artifact",
            "run_id",
            "generated_at",
            "producer",
            "inputs",
            "template_path",
            "output_docx",
            "review_decision",
            "placeholders",
            "assembly_status",
        ]
        missing = [field for field in required if field not in manifest]
        if missing:
            raise RuntimeError(f"assembly-manifest.json 缺少字段：{', '.join(missing)}")
        if manifest["schema_version"] != SCHEMA_VERSION or manifest["artifact"] != "assembly-manifest":
            raise RuntimeError("assembly-manifest.json artifact 或 schema_version 不符合契约。")
        if manifest["producer"].get("agent") != AGENT_NAME:
            raise RuntimeError("assembly-manifest.json producer.agent 不符合契约。")
        if manifest["review_decision"] not in {"approved", "blocked"}:
            raise RuntimeError("assembly-manifest.json review_decision 不符合契约。")
        if manifest["assembly_status"] not in {"generated", "blocked", "failed", "published"}:
            raise RuntimeError("assembly-manifest.json assembly_status 不符合契约。")
        valid_placeholder_status = {"filled", "preserved_confirm", "review_inserted", "missing", "skipped", "failed"}
        for placeholder in manifest["placeholders"]:
            if placeholder.get("status") not in valid_placeholder_status:
                raise RuntimeError(f"assembly-manifest.json placeholder status 无效：{placeholder}")

    def render_placeholder_log(self) -> str:
        lines = [
            "# Placeholder Fill Log",
            "",
            f"- Run ID: {self.run_id}",
            f"- Generated At: {self.generated_at}",
            "",
            "| Placeholder | Status | Source | Message |",
            "|---|---|---|---|",
        ]
        for item in self.placeholders:
            lines.append(
                f"| {self.escape_md(item['placeholder'])} | {item['status']} | {self.escape_md(item['source'])} | {self.escape_md(item.get('message', ''))} |"
            )
        lines.append("")
        return "\n".join(lines)

    def render_assembly_log(self, manifest: dict[str, Any]) -> str:
        lines = [
            "# Assembly Log",
            "",
            f"- Run ID: {self.run_id}",
            f"- Generated At: {self.generated_at}",
            f"- Review Decision: {manifest['review_decision']}",
            f"- Assembly Status: {manifest['assembly_status']}",
            f"- Template: {manifest['template_path']}",
            f"- Output Docx: {manifest['output_docx']}",
            "",
            "## Inputs",
            "",
        ]
        for item in manifest["inputs"]:
            lines.append(f"- {item['artifact']}: {item['path']} ({item['schema_version']})")
        lines.extend(["", "## Events", ""])
        if self.events:
            lines.extend(f"- {event}" for event in self.events)
        else:
            lines.append("- 无。")
        lines.extend(["", "## Failures", ""])
        if self.failures:
            lines.extend(f"- {failure}" for failure in self.failures)
        else:
            lines.append("- 无。")
        lines.append("")
        return "\n".join(lines)

    def render_residual_check(self) -> str:
        lines = [
            "# Residual Placeholder Check",
            "",
            f"- Run ID: {self.run_id}",
            f"- Generated At: {self.generated_at}",
            "",
            "| Placeholder | Kind | Status |",
            "|---|---|---|",
        ]
        if self.residuals:
            for item in self.residuals:
                lines.append(f"| {self.escape_md(item['placeholder'])} | {item['kind']} | {item['status']} |")
        else:
            lines.append("| - | - | none |")
        lines.append("")
        return "\n".join(lines)

    def add_placeholder(self, placeholder: str, status: str, source: str, message: str) -> None:
        self.placeholders.append(
            {
                "placeholder": placeholder,
                "status": status,
                "source": source,
                "message": message,
            }
        )

    def confirm_source(self, placeholder: str, content_blocks: dict[str, Any]) -> str:
        for block in content_blocks.get("blocks", []):
            if block.get("placeholder") == placeholder:
                return str(block.get("block_id") or "content-blocks.json")
        for item in content_blocks.get("confirm_items", []):
            if placeholder in str(item.get("message", "")):
                return str(item.get("item_id") or "content-blocks.json")
        return "confirm_items"

    def resolve_workspace_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return self.workspace / path

    @staticmethod
    def project_name(requirements: dict[str, Any]) -> str:
        project = requirements.get("project") or {}
        return str(project.get("name") or project.get("tender_name") or "投标方案")

    @staticmethod
    def safe_filename(value: str) -> str:
        invalid = set('<>:"/\\|?*')
        cleaned = "".join("_" if char in invalid or ord(char) < 32 else char for char in value)
        return cleaned.strip(". ") or "投标方案"

    @staticmethod
    def shorten(value: str, max_length: int) -> str:
        value = re.sub(r"\s+", " ", str(value)).strip()
        if len(value) <= max_length:
            return value
        return value[: max_length - 1] + "…"

    @staticmethod
    def escape_md(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace.resolve())).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    @staticmethod
    def xml_bytes(root: ET.Element) -> bytes:
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Word Layout Agent.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root.")
    parser.add_argument("--records-dir", type=Path, default=Path("output/records"), help="Directory containing approved published artifacts.")
    parser.add_argument("--template-path", type=Path, default=Path("templates/投标方案模板.docx"), help="Word template .docx path.")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Directory for final Word output.")
    parser.add_argument("--records-output-dir", type=Path, default=Path("output/records"), help="Directory for assembly record output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    workspace = args.workspace.resolve()
    records_dir = args.records_dir if args.records_dir.is_absolute() else workspace / args.records_dir
    template_path = args.template_path if args.template_path.is_absolute() else workspace / args.template_path
    output_dir = args.output_dir if args.output_dir.is_absolute() else workspace / args.output_dir
    records_output_dir = args.records_output_dir if args.records_output_dir.is_absolute() else workspace / args.records_output_dir

    agent = WordLayoutAgent(
        workspace=workspace,
        records_dir=records_dir,
        template_path=template_path,
        output_dir=output_dir,
        records_output_dir=records_output_dir,
    )
    result = agent.run()
    print(f"Word Layout Agent completed: {agent.run_id}")
    print(f"status: {result['status']}")
    print(f"staging: {result['staging_dir']}")
    print(f"published: {result['published_dir']}")
    print(f"records: {result['records_output_dir']}")
    if result.get("output_docx"):
        print(f"output_docx: {result['output_docx']}")
    if result["status"] == "blocked":
        return 2
    if result["status"] == "failed":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
