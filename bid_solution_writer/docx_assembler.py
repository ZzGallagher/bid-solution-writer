from __future__ import annotations

import copy
import mimetypes
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .models import ContentBlock, DiagramSpec


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
WORD_NS = {"w": W_NS}

for prefix, uri in (("w", W_NS), ("r", R_NS), ("wp", WP_NS), ("a", A_NS), ("pic", PIC_NS)):
    ET.register_namespace(prefix, uri)


def qn(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


def assemble_docx(template_path: Path, output_path: Path, blocks: list[ContentBlock], diagrams: list[DiagramSpec]) -> None:
    if not template_path.exists():
        raise FileNotFoundError(f"Word 金样板不存在：{template_path}")
    with zipfile.ZipFile(template_path, "r") as archive:
        package_files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    root = ET.fromstring(package_files["word/document.xml"])
    body = root.find(qn(W_NS, "body"))
    if body is None:
        raise RuntimeError("Word 文档缺少正文 body。")

    children = list(body)
    start_index = find_child_index(children, "概述")
    end_index = find_child_index(children, "数据库设计")
    if start_index is None or end_index is None or end_index <= start_index:
        raise RuntimeError("无法在示例输出.docx 中定位“概述”到“数据库设计”的替换范围。")

    style_templates = collect_style_templates(children)
    rels_root = load_relationships(package_files)
    content_types_root = load_content_types(package_files)
    media_files: dict[str, bytes] = {}
    diagram_by_id = {diagram.diagram_id: diagram for diagram in diagrams}

    new_elements = build_body_elements(blocks, diagram_by_id, style_templates, rels_root, content_types_root, media_files)
    body[:] = children[:start_index] + new_elements + children[end_index:]

    package_files["word/document.xml"] = xml_bytes(root)
    package_files["word/_rels/document.xml.rels"] = xml_bytes(rels_root)
    package_files["[Content_Types].xml"] = xml_bytes(content_types_root)
    package_files.update(media_files)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in package_files.items():
            archive.writestr(name, data)


def build_body_elements(
    blocks: list[ContentBlock],
    diagrams: dict[str, DiagramSpec],
    style_templates: dict[str, ET.Element],
    rels_root: ET.Element,
    content_types_root: ET.Element,
    media_files: dict[str, bytes],
) -> list[ET.Element]:
    elements: list[ET.Element] = []
    for block in blocks:
        if block.content_type == "heading1":
            elements.append(make_paragraph(block.title, style_templates.get("heading1")))
        elif block.content_type == "heading2":
            elements.append(make_paragraph(block.title, style_templates.get("heading2")))
        elif block.content_type == "heading3":
            elements.append(make_paragraph(block.title, style_templates.get("heading3")))
        elif block.content_type == "heading4":
            elements.append(make_paragraph(block.title, style_templates.get("heading4")))
        elif block.content_type == "paragraphs":
            for paragraph in block.content:
                elements.append(make_paragraph(paragraph, style_templates.get("body")))
        elif block.content_type == "diagram":
            diagram = diagrams.get(block.diagram_id or "")
            if not diagram or not diagram.image_path:
                raise RuntimeError(f"图表 {block.diagram_id} 未成功渲染，停止 Word 装配。")
            rel_id = add_image_relationship(Path(diagram.image_path), rels_root, content_types_root, media_files)
            width, height = image_dimensions(Path(diagram.image_path))
            elements.append(make_image_paragraph(rel_id, diagram.title, width, height))
            elements.append(make_paragraph(diagram.title, style_templates.get("caption")))
        else:
            raise RuntimeError(f"未知 Word 内容块类型：{block.content_type}")
    return elements


def find_child_index(children: list[ET.Element], text: str) -> int | None:
    for index, child in enumerate(children):
        if element_text(child).strip() == text:
            return index
    return None


def collect_style_templates(children: list[ET.Element]) -> dict[str, ET.Element]:
    labels = {
        "概述": "heading1",
        "建设背景": "heading2",
        "多模式数据导入": "heading3",
        "系统架构图": "caption",
    }
    templates: dict[str, ET.Element] = {}
    first_body: ET.Element | None = None
    for child in children:
        if child.tag != qn(W_NS, "p"):
            continue
        text = element_text(child).strip()
        if first_body is None and len(text) > 40:
            first_body = child
        if text in labels:
            templates[labels[text]] = child
    if first_body is not None:
        templates["body"] = first_body
    return templates


def element_text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.findall(".//w:t", WORD_NS))


def make_paragraph(text: str, template: ET.Element | None = None) -> ET.Element:
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


def make_image_paragraph(relationship_id: str, title: str, width_px: int, height_px: int) -> ET.Element:
    max_cx = 5486400
    width_px = width_px or 1200
    height_px = height_px or 800
    ratio = height_px / width_px if width_px else 0.67
    cx = max_cx
    cy = int(cx * ratio)
    paragraph = ET.Element(qn(W_NS, "p"))
    run = ET.SubElement(paragraph, qn(W_NS, "r"))
    drawing = ET.SubElement(run, qn(W_NS, "drawing"))
    inline = ET.SubElement(drawing, qn(WP_NS, "inline"))
    for key in ("distT", "distB", "distL", "distR"):
        inline.set(key, "0")
    ET.SubElement(inline, qn(WP_NS, "extent"), {"cx": str(cx), "cy": str(cy)})
    effect_extent = ET.SubElement(inline, qn(WP_NS, "effectExtent"))
    for key in ("l", "t", "r", "b"):
        effect_extent.set(key, "0")
    ET.SubElement(inline, qn(WP_NS, "docPr"), {"id": "1001", "name": title})
    cnv = ET.SubElement(inline, qn(WP_NS, "cNvGraphicFramePr"))
    ET.SubElement(cnv, qn(A_NS, "graphicFrameLocks"), {"noChangeAspect": "1"})
    graphic = ET.SubElement(inline, qn(A_NS, "graphic"))
    graphic_data = ET.SubElement(graphic, qn(A_NS, "graphicData"), {"uri": "http://schemas.openxmlformats.org/drawingml/2006/picture"})
    pic = ET.SubElement(graphic_data, qn(PIC_NS, "pic"))
    nv_pic_pr = ET.SubElement(pic, qn(PIC_NS, "nvPicPr"))
    ET.SubElement(nv_pic_pr, qn(PIC_NS, "cNvPr"), {"id": "0", "name": title})
    ET.SubElement(nv_pic_pr, qn(PIC_NS, "cNvPicPr"))
    blip_fill = ET.SubElement(pic, qn(PIC_NS, "blipFill"))
    blip = ET.SubElement(blip_fill, qn(A_NS, "blip"))
    blip.set(qn(R_NS, "embed"), relationship_id)
    stretch = ET.SubElement(blip_fill, qn(A_NS, "stretch"))
    ET.SubElement(stretch, qn(A_NS, "fillRect"))
    sp_pr = ET.SubElement(pic, qn(PIC_NS, "spPr"))
    xfrm = ET.SubElement(sp_pr, qn(A_NS, "xfrm"))
    ET.SubElement(xfrm, qn(A_NS, "off"), {"x": "0", "y": "0"})
    ET.SubElement(xfrm, qn(A_NS, "ext"), {"cx": str(cx), "cy": str(cy)})
    prst_geom = ET.SubElement(sp_pr, qn(A_NS, "prstGeom"), {"prst": "rect"})
    ET.SubElement(prst_geom, qn(A_NS, "avLst"))
    return paragraph


def add_image_relationship(image_path: Path, rels_root: ET.Element, content_types_root: ET.Element, media_files: dict[str, bytes]) -> str:
    rel_id = next_relationship_id(rels_root)
    suffix = image_path.suffix.lower() or ".png"
    media_index = len(media_files) + 100
    media_name = f"image{media_index}{suffix}"
    target = f"media/{media_name}"
    relationship = ET.Element(qn(REL_NS, "Relationship"))
    relationship.set("Id", rel_id)
    relationship.set("Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image")
    relationship.set("Target", target)
    rels_root.append(relationship)
    media_files[f"word/{target}"] = image_path.read_bytes()
    ensure_content_type(content_types_root, suffix)
    return rel_id


def next_relationship_id(rels_root: ET.Element) -> str:
    max_id = 0
    for relationship in rels_root.findall(qn(REL_NS, "Relationship")):
        rid = relationship.get("Id", "")
        if rid.startswith("rId") and rid[3:].isdigit():
            max_id = max(max_id, int(rid[3:]))
    return f"rId{max_id + 1}"


def load_relationships(package_files: dict[str, bytes]) -> ET.Element:
    data = package_files.get("word/_rels/document.xml.rels")
    return ET.fromstring(data) if data else ET.Element(qn(REL_NS, "Relationships"))


def load_content_types(package_files: dict[str, bytes]) -> ET.Element:
    data = package_files.get("[Content_Types].xml")
    return ET.fromstring(data) if data else ET.Element(qn(CT_NS, "Types"))


def ensure_content_type(root: ET.Element, suffix: str) -> None:
    extension = suffix.lstrip(".").lower()
    for item in root.findall(qn(CT_NS, "Default")):
        if item.get("Extension", "").lower() == extension:
            return
    default = ET.Element(qn(CT_NS, "Default"))
    default.set("Extension", extension)
    default.set("ContentType", mimetypes.types_map.get(f".{extension}", "application/octet-stream"))
    root.append(default)


def image_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:32]
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return 1200, 800


def xml_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
