from __future__ import annotations

import re
from pathlib import Path

from .models import FunctionGroup, FunctionPoint, ParsedRequirements, RequirementSection


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def parse_markdown(path: Path) -> ParsedRequirements:
    text = path.read_text(encoding="utf-8")
    sections = split_sections(text)

    project_overview = required_section(sections, "项目概述").strip()
    function_body = required_section(sections, "功能要求").strip()
    performance_body = required_section(sections, "性能要求").strip()
    non_functional_body = required_section(sections, "非功能性要求").strip()

    return ParsedRequirements(
        project_name=project_name_from_path(path),
        project_overview=project_overview,
        function_requirements=RequirementSection("功能要求", function_body),
        performance_requirements=RequirementSection("性能要求", performance_body),
        non_functional_requirements=RequirementSection("非功能性要求", non_functional_body),
        function_groups=parse_function_groups(function_body),
        performance_items=parse_performance_items(performance_body),
    )


def split_sections(text: str) -> dict[str, str]:
    lines = text.splitlines()
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for raw in lines:
        match = HEADING_RE.match(raw)
        if match and len(match.group(1)) == 1:
            current = match.group(2).strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(raw)
    return {title: "\n".join(body).strip() for title, body in sections.items()}


def required_section(sections: dict[str, str], title: str) -> str:
    if title not in sections or not sections[title].strip():
        raise ValueError(f"技术要求 Markdown 缺少必需章节：{title}")
    return sections[title]


def parse_function_groups(body: str) -> list[FunctionGroup]:
    groups: list[FunctionGroup] = []
    current_group: tuple[int, str, list[str]] | None = None
    current_point: tuple[str, list[str]] | None = None

    def flush_point() -> None:
        nonlocal current_group, current_point
        if current_group and current_point:
            current_group[2].append(FunctionPoint(current_point[0], "\n".join(current_point[1]).strip()))
        current_point = None

    def flush_group() -> None:
        nonlocal current_group
        flush_point()
        if current_group:
            groups.append(FunctionGroup(current_group[0], current_group[1], list(current_group[2])))
        current_group = None

    for raw in body.splitlines():
        line = raw.rstrip()
        match = HEADING_RE.match(line)
        if match and len(match.group(1)) == 2:
            flush_group()
            title = match.group(2).strip()
            number_match = re.match(r"^(\d+)[.、]\s*(.+)$", title)
            index = int(number_match.group(1)) if number_match else len(groups) + 1
            clean_title = number_match.group(2).strip() if number_match else title
            current_group = (index, clean_title, [])
            continue
        if match and len(match.group(1)) == 3:
            flush_point()
            current_point = (match.group(2).strip(), [])
            continue
        if current_point is not None and line.strip():
            current_point[1].append(line.strip())

    flush_group()
    if len(groups) != 5:
        raise ValueError(f"功能要求应解析为 5 个一级功能点，实际为 {len(groups)}")
    point_count = sum(len(group.points) for group in groups)
    if point_count != 11:
        raise ValueError(f"功能要求应解析为 11 个子功能点，实际为 {point_count}")
    return groups


def parse_performance_items(body: str) -> list[str]:
    items = []
    for raw in body.splitlines():
        line = raw.strip()
        match = re.match(r"^\d+[.)、]\s*(.+)$", line)
        if match:
            items.append(match.group(1).strip())
    if not items:
        raise ValueError("性能要求章节未解析到编号条目。")
    return items


def project_name_from_path(path: Path) -> str:
    name = path.stem
    return re.sub(r"(技术要求|需求规格|需求)$", "", name).strip("-_ 　") or path.stem
