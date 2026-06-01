from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RequirementSection:
    title: str
    body: str


@dataclass(frozen=True)
class FunctionPoint:
    title: str
    body: str


@dataclass(frozen=True)
class FunctionGroup:
    index: int
    title: str
    points: list[FunctionPoint] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedRequirements:
    project_name: str
    project_overview: str
    function_requirements: RequirementSection
    performance_requirements: RequirementSection
    non_functional_requirements: RequirementSection
    function_groups: list[FunctionGroup]
    performance_items: list[str]


@dataclass(frozen=True)
class ContentBlock:
    section_id: str
    title: str
    content_type: str
    content: list[str]
    diagram_id: str | None = None


@dataclass(frozen=True)
class DiagramSpec:
    diagram_id: str
    title: str
    kind: str
    section_id: str
    mermaid: str
    mermaid_path: str
    image_path: str | None = None
    render_status: str = "pending"
    error: str | None = None
