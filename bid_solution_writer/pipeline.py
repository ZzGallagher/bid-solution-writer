from __future__ import annotations

import json
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .content_generator import ContentGenerator
from .diagram_renderer import render_diagrams
from .docx_assembler import assemble_docx
from .markdown_parser import parse_markdown
from .mermaid_generator import build_architecture_diagram, build_function_diagram, write_mermaid_files
from .models import ContentBlock, DiagramSpec, FunctionGroup, ParsedRequirements
from .workflow import workflow_payload


def generate(
    input_path: Path,
    template_path: Path,
    output_path: Path,
    records_dir: Path,
    renderer_command: str | None = None,
    allow_local_draft: bool = False,
) -> dict:
    run_id = f"RUN-{datetime.now().astimezone():%Y%m%d-%H%M%S}"
    records_dir.mkdir(parents=True, exist_ok=True)
    diagrams_dir = records_dir / "diagrams"
    diagrams_dir.mkdir(parents=True, exist_ok=True)

    parsed = parse_markdown(input_path)
    generator = ContentGenerator(allow_local_draft=allow_local_draft)
    blocks: list[ContentBlock] = []
    diagrams: list[DiagramSpec] = []

    blocks.extend(build_intro_blocks(generator, parsed))
    blocks.extend(build_requirement_blocks(parsed))
    architecture_diagram = build_architecture_diagram(parsed, diagrams_dir)
    diagrams.append(architecture_diagram)
    blocks.extend(build_architecture_blocks(generator, parsed, architecture_diagram))
    function_blocks, function_diagrams = build_function_blocks(generator, parsed, diagrams_dir)
    blocks.extend(function_blocks)
    diagrams.extend(function_diagrams)
    blocks.extend(build_performance_blocks(generator, parsed))

    write_mermaid_files(diagrams)
    diagrams = render_diagrams(diagrams, renderer_command=renderer_command)
    failed = [diagram for diagram in diagrams if diagram.render_status == "failed"]
    write_records(records_dir, run_id, parsed, blocks, diagrams, input_path, template_path, output_path, failed)
    if failed:
        names = ", ".join(f"{diagram.diagram_id}: {diagram.error}" for diagram in failed[:5])
        raise RuntimeError(f"Mermaid 渲染失败，已保留 .mmd 源码，未生成最终 Word：{names}")

    assemble_docx(template_path, output_path, blocks, diagrams)
    write_records(records_dir, run_id, parsed, blocks, diagrams, input_path, template_path, output_path, [])
    return {"run_id": run_id, "output": str(output_path), "records_dir": str(records_dir)}


def build_intro_blocks(generator: ContentGenerator, parsed: ParsedRequirements) -> list[ContentBlock]:
    return [
        ContentBlock("1", "概述", "heading1", []),
        ContentBlock("1.1", "建设背景", "heading2", []),
        ContentBlock("1.1.content", "建设背景正文", "paragraphs", generator.background(parsed)),
        ContentBlock("1.2", "编写依据", "heading2", []),
        ContentBlock("1.2.content", "编写依据正文", "paragraphs", ["引用规范和标准如下所示。"]),
    ]


def build_requirement_blocks(parsed: ParsedRequirements) -> list[ContentBlock]:
    return [
        ContentBlock("2", "需求分析", "heading1", []),
        ContentBlock("2.1", "功能需求", "heading2", []),
        ContentBlock("2.1.content", "功能需求正文", "paragraphs", markdown_body_to_paragraphs(parsed.function_requirements.body)),
        ContentBlock("2.2", "性能要求", "heading2", []),
        ContentBlock("2.2.content", "性能要求正文", "paragraphs", markdown_body_to_paragraphs(parsed.performance_requirements.body)),
        ContentBlock("2.3", "非功能性要求", "heading2", []),
        ContentBlock("2.3.content", "非功能性要求正文", "paragraphs", markdown_body_to_paragraphs(parsed.non_functional_requirements.body)),
    ]


def build_architecture_blocks(generator: ContentGenerator, parsed: ParsedRequirements, diagram: DiagramSpec) -> list[ContentBlock]:
    return [
        ContentBlock("3", "软件设计", "heading1", []),
        ContentBlock("3.1", "架构设计", "heading2", []),
        ContentBlock("3.1.content", "架构设计正文", "paragraphs", generator.architecture(parsed)),
        ContentBlock("3.1.diagram.note", "系统架构图说明", "paragraphs", ["系统架构图如下所示。"]),
        ContentBlock("3.1.diagram", diagram.title, "diagram", [], diagram.diagram_id),
    ]


def build_function_blocks(generator: ContentGenerator, parsed: ParsedRequirements, diagrams_dir: Path) -> tuple[list[ContentBlock], list[DiagramSpec]]:
    blocks = [ContentBlock("3.2", "功能设计", "heading2", [])]
    diagrams: list[DiagramSpec] = []
    diagram_index = 2
    for group in parsed.function_groups:
        group_id = f"3.2.{group.index}"
        blocks.append(ContentBlock(group_id, group.title, "heading3", []))
        for point_index, point in enumerate(group.points, 1):
            section_id = f"{group_id}.{point_index}"
            diagram_id = f"DG{diagram_index:03d}"
            diagram_index += 1
            diagram = build_function_diagram(diagram_id, section_id, group.title, point, diagrams_dir)
            diagrams.append(diagram)
            blocks.append(ContentBlock(section_id, point.title, "heading4", []))
            blocks.append(ContentBlock(f"{section_id}.content", point.title, "paragraphs", generator.function_design(group.title, point, section_id)))
            blocks.append(ContentBlock(f"{section_id}.diagram", diagram.title, "diagram", [], diagram.diagram_id))
    return blocks, diagrams


def build_performance_blocks(generator: ContentGenerator, parsed: ParsedRequirements) -> list[ContentBlock]:
    return [
        ContentBlock("3.3", "性能设计", "heading2", []),
        ContentBlock("3.3.content", "性能设计正文", "paragraphs", generator.performance(parsed)),
    ]


def markdown_body_to_paragraphs(body: str) -> list[str]:
    paragraphs = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        paragraphs.append(line)
    return paragraphs


def write_records(
    records_dir: Path,
    run_id: str,
    parsed: ParsedRequirements,
    blocks: list[ContentBlock],
    diagrams: list[DiagramSpec],
    input_path: Path,
    template_path: Path,
    output_path: Path,
    failed_diagrams: list[DiagramSpec],
) -> None:
    generation_map = {
        "run_id": run_id,
        "project_name": parsed.project_name,
        "writing_workflow": workflow_payload(),
        "sections": [{"section_id": block.section_id, "title": block.title, "type": block.content_type, "diagram_id": block.diagram_id} for block in blocks],
        "rules": {
            "completed_until": "3.3 性能设计",
            "remaining_sections": "3.3 后续章节仅保留标题，代码留空。",
        },
    }
    content_blocks = {"run_id": run_id, "blocks": [asdict(block) for block in blocks]}
    diagram_specs = {
        "run_id": run_id,
        "diagrams": [asdict(diagram) for diagram in diagrams],
    }
    manifest = {
        "run_id": run_id,
        "status": "failed" if failed_diagrams else "generated",
        "input": str(input_path),
        "template": str(template_path),
        "output": str(output_path),
        "records_dir": str(records_dir),
        "failed_diagrams": [asdict(diagram) for diagram in failed_diagrams],
    }
    for name, data in (
        ("generation-map.json", generation_map),
        ("content-blocks.json", content_blocks),
        ("diagram-specs.json", diagram_specs),
        ("run-manifest.json", manifest),
    ):
        (records_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
