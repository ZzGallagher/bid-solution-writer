from __future__ import annotations

import json
import re
import subprocess
import textwrap
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from shutil import copy2, which

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from docx.text.paragraph import Paragraph
from PIL import Image, ImageDraw, ImageFont

from llm_client import LLMConfigError, OpenAICompatibleClient, OpenAICompatibleConfig
from llm_diagram_generator import (
    DiagramGenerationError,
    DiagramRequest,
    DiagramSpec,
    build_prompts,
    generate_diagram_spec,
    read_examples,
    save_prompt,
)


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "input"
TEMPLATE = ROOT / "templates" / "投标方案模板.docx"
OUTPUT = ROOT / "output"
RECORDS = OUTPUT / "records"
TODAY = datetime.now().strftime("%Y%m%d")


@dataclass
class Requirement:
    id: str
    source_file: str
    category: str
    title: str
    text: str
    keywords: list[str] = field(default_factory=list)
    target_section: str = ""
    need_diagram: bool = False
    status: str = "待生成"


@dataclass
class ProjectData:
    project_name: str
    standards: list[str]
    function_requirements: list[Requirement]
    performance_requirements: list[Requirement]
    quality_requirements: list[Requirement]
    business_requirements: list[Requirement]
    scoring_items: list[Requirement]
    delivery_rows: list[list[str]]


@dataclass
class DiagramAsset:
    title: str
    mmd_path: Path
    image_path: Path
    description: str
    source_requirement_ids: list[str] = field(default_factory=list)
    review_notes: list[str] = field(default_factory=list)
    render_status: str = ""


def ensure_dirs() -> None:
    OUTPUT.mkdir(exist_ok=True)
    RECORDS.mkdir(exist_ok=True)
    for pattern in ("*.mmd", "*.png"):
        for path in RECORDS.glob(pattern):
            path.unlink()


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def docx_paragraphs(path: Path) -> list[str]:
    doc = Document(str(path))
    return [clean_text(p.text) for p in doc.paragraphs if clean_text(p.text)]


def docx_tables(path: Path) -> list[list[list[str]]]:
    doc = Document(str(path))
    tables: list[list[list[str]]] = []
    for table in doc.tables:
        rows: list[list[str]] = []
        for row in table.rows:
            rows.append([clean_text(cell.text) for cell in row.cells])
        if rows:
            tables.append(rows)
    return tables


def split_requirement_text(text: str) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    parts = re.split(r"(?=\d+[.、])", text)
    if len(parts) > 1:
        return [clean_text(p) for p in parts if len(clean_text(p)) > 8]
    return [text]


def looks_like_heading(text: str) -> bool:
    if not text or len(text) > 28:
        return False
    if re.search(r"[。；;：:，,]", text):
        return False
    return True


def guess_project_name(paragraphs: list[str]) -> str:
    for text in paragraphs[:8]:
        if text not in {"技术要求", "商务要求"} and not text.startswith("一、"):
            return text.replace("技术要求", "").strip() or "待确认项目"
    return "待确认项目"


def keywords_for(text: str, fallback: str = "") -> list[str]:
    known = [
        "架构",
        "功能",
        "性能",
        "安全",
        "接口",
        "数据",
        "部署",
        "质量",
        "测试",
        "培训",
        "售后",
        "交付",
        "验收",
        "风险",
        "运维",
        "保密",
        "标准",
    ]
    hits = [word for word in known if word in text]
    if not hits and fallback:
        hits.append(fallback)
    return hits[:4]


def classify_target(title: str, text: str, category: str) -> str:
    merged = title + text
    if category == "商务要求":
        if any(word in merged for word in ["培训", "售后", "质保", "服务"]):
            return "售后服务及承诺"
        if any(word in merged for word in ["交付", "验收"]):
            return "成果交付及验收"
        return "商务响应与复核"
    if category == "评分项":
        if "架构" in merged:
            return "总体架构设计"
        if "功能" in merged:
            return "功能设计章节"
        if "风险" in merged:
            return "风险评估与控制"
        if "质量" in merged:
            return "质量控制总述"
        if "培训" in merged or "售后" in merged:
            return "培训方案"
        return "评分响应强化"
    if "性能" in category:
        return "性能设计章节"
    if "质量" in category or title in {"可靠性", "维修性", "保障性", "测试性", "安全性", "环境适应性"}:
        return f"{title}设计" if title else "通用质量特性设计"
    return f"{title}功能" if title else "功能设计章节"


def parse_technical_requirements(path: Path) -> tuple[str, list[str], list[Requirement], list[Requirement], list[Requirement], list[list[str]]]:
    paragraphs = docx_paragraphs(path)
    project_name = guess_project_name(paragraphs)
    standards: list[str] = []
    functions: list[Requirement] = []
    performance: list[Requirement] = []
    quality: list[Requirement] = []

    section = ""
    current_title = ""
    standard_mode = False
    rid = 1

    section_names = {"执行的标准", "功能要求", "性能要求", "通用质量特性要求", "软件交付内容"}
    quality_headings = {"可靠性要求", "安全性要求", "保障性要求", "维修性要求", "测试性要求", "环境适应性要求", "质量监督要求"}
    for text in paragraphs:
        if "非功能性要求" in text:
            section = "通用质量特性要求"
            current_title = "通用质量特性"
            continue
        if text in section_names:
            section = text
            standard_mode = section == "执行的标准"
            current_title = ""
            continue
        if text in quality_headings:
            section = "通用质量特性要求"
            current_title = text.removesuffix("要求")
            continue
        if text in {"技术要求", project_name, "本规格书执行下列法规、标准规范等。其最新版本适用于本规格书。"}:
            continue

        if standard_mode and section == "执行的标准":
            if text == "功能要求":
                section = text
                standard_mode = False
            elif len(text) > 8:
                standards.append(text)
            continue

        if section == "功能要求":
            if looks_like_heading(text):
                current_title = text
                continue
            for item in split_requirement_text(text):
                functions.append(
                    Requirement(
                        id=f"T{rid:03d}",
                        source_file=path.name,
                        category="技术要求-功能",
                        title=current_title or "功能要求",
                        text=item,
                        keywords=keywords_for(item, current_title),
                        target_section=classify_target(current_title, item, "技术要求-功能"),
                        need_diagram=True,
                        status="已抽取",
                    )
                )
                rid += 1
            continue

        if section == "性能要求":
            if looks_like_heading(text) and len(text) <= 12:
                current_title = text
                continue
            for item in split_requirement_text(text):
                performance.append(
                    Requirement(
                        id=f"P{len(performance) + 1:03d}",
                        source_file=path.name,
                        category="技术要求-性能",
                        title=current_title or "性能",
                        text=item,
                        keywords=keywords_for(item, "性能"),
                        target_section="性能设计章节",
                        status="已抽取",
                    )
                )
            continue

        if section == "通用质量特性要求":
            if looks_like_heading(text):
                current_title = text
                continue
            for item in split_requirement_text(text):
                quality.append(
                    Requirement(
                        id=f"Q{len(quality) + 1:03d}",
                        source_file=path.name,
                        category="技术要求-质量",
                        title=current_title or "通用质量特性",
                        text=item,
                        keywords=keywords_for(item, current_title or "质量"),
                        target_section=classify_target(current_title, item, "技术要求-质量"),
                        status="已抽取",
                    )
                )

    delivery_rows = docx_tables(path)[0] if docx_tables(path) else []
    return project_name, standards, functions, performance, quality, delivery_rows


def parse_business_requirements(path: Path) -> list[Requirement]:
    rows = docx_tables(path)[0] if docx_tables(path) else []
    requirements: list[Requirement] = []
    for index, row in enumerate(rows[1:], 1):
        if len(row) < 4:
            continue
        title = row[2] or f"商务条款{index}"
        for part in split_requirement_text(row[3]):
            requirements.append(
                Requirement(
                    id=f"B{len(requirements) + 1:03d}",
                    source_file=path.name,
                    category="商务要求",
                    title=title,
                    text=part,
                    keywords=keywords_for(title + part, "商务"),
                    target_section=classify_target(title, part, "商务要求"),
                    status="已抽取",
                )
            )
    return requirements


def parse_scoring_items(path: Path) -> list[Requirement]:
    rows = docx_tables(path)[0] if docx_tables(path) else []
    items: list[Requirement] = []
    for row in rows[2:]:
        if len(row) < 6 or not row[2]:
            continue
        title = row[1] or "评分项"
        score = row[3]
        text = f"{row[2]}（分值：{score}）" if score else row[2]
        items.append(
            Requirement(
                id=f"S{len(items) + 1:03d}",
                source_file=path.name,
                category="评分项",
                title=title,
                text=text,
                keywords=keywords_for(title + text, "评分"),
                target_section=classify_target(title, text, "评分项"),
                status="已抽取",
            )
        )
    return items


def load_project_data() -> ProjectData:
    tech_path = INPUT / "技术要求.docx"
    business_path = INPUT / "商务要求.docx"
    scoring_path = INPUT / "技术评分表.docx"

    project_name, standards, functions, performance, quality, delivery_rows = parse_technical_requirements(tech_path)
    return ProjectData(
        project_name=project_name,
        standards=standards,
        function_requirements=functions,
        performance_requirements=performance,
        quality_requirements=quality,
        business_requirements=parse_business_requirements(business_path),
        scoring_items=parse_scoring_items(scoring_path),
        delivery_rows=delivery_rows,
    )


def group_by_title(requirements: list[Requirement]) -> dict[str, list[Requirement]]:
    grouped: dict[str, list[Requirement]] = {}
    for req in requirements:
        grouped.setdefault(req.title or "未分组", []).append(req)
    return grouped


def paragraph(text: str, style: str = "正文") -> dict:
    return {"type": "paragraph", "text": clean_text(text), "style": style}


def heading(text: str, level: int = 3) -> dict:
    return {"type": "heading", "text": text, "level": level}


def table(rows: list[list[str]]) -> dict:
    return {"type": "table", "rows": rows}


def image(path: Path, width: float = 5.8) -> dict:
    return {"type": "image", "path": str(path), "width": width}


def caption(text: str) -> dict:
    return {"type": "caption", "text": text}


def prose_blocks(text: str) -> list[dict]:
    return [paragraph(p) for p in re.split(r"\n+", text.strip()) if clean_text(p)]


def requirement_bullets(requirements: list[Requirement], limit: int | None = None) -> str:
    items = requirements[:limit] if limit else requirements
    return "\n".join(f"{idx}. {req.text}" for idx, req in enumerate(items, 1))


def font_path() -> Path | None:
    candidates = [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
        Path(r"C:\Windows\Fonts\simhei.ttf"),
        Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
    ]
    return next((path for path in candidates if path.exists()), None)


def draw_flow_png(path: Path, title: str, nodes: list[str]) -> None:
    width = 1600
    height = 260 + max(1, len(nodes)) * 110
    img = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(img)
    fp = font_path()
    title_font = ImageFont.truetype(str(fp), 32) if fp else ImageFont.load_default()
    text_font = ImageFont.truetype(str(fp), 22) if fp else ImageFont.load_default()

    draw.text((width // 2, 48), title, fill="#1f2937", font=title_font, anchor="mm")
    x = width // 2
    y = 130
    box_w = 560
    box_h = 62
    for index, node in enumerate(nodes):
        top = y + index * 100
        draw.rounded_rectangle((x - box_w // 2, top, x + box_w // 2, top + box_h), radius=12, outline="#2563eb", width=3, fill="#eff6ff")
        wrapped = textwrap.wrap(node, width=28)[:2]
        for line_index, line in enumerate(wrapped):
            draw.text((x, top + 23 + line_index * 24), line, fill="#111827", font=text_font, anchor="mm")
        if index < len(nodes) - 1:
            draw.line((x, top + box_h, x, top + 96), fill="#64748b", width=3)
            draw.polygon([(x - 8, top + 96), (x + 8, top + 96), (x, top + 108)], fill="#64748b")
    img.save(path)


def safe_filename(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text)
    return text[:60].strip("- ") or "diagram"


def requirement_payload(req: Requirement, *, max_text: int = 360) -> dict:
    text = req.text if len(req.text) <= max_text else req.text[:max_text].rstrip() + "……"
    return {
        "id": req.id,
        "source_file": req.source_file,
        "category": req.category,
        "title": req.title,
        "text": text,
        "keywords": req.keywords,
        "target_section": req.target_section,
    }


def reset_generated_files(path: Path, suffixes: tuple[str, ...]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for item in path.iterdir():
        if item.is_file() and item.suffix.lower() in suffixes:
            item.unlink()


def build_diagram_requests(data: ProjectData) -> list[DiagramRequest]:
    grouped_functions = group_by_title(data.function_requirements)
    architecture_examples = read_examples(ROOT / "templates" / "architecture_examples")
    flowchart_examples = read_examples(ROOT / "templates" / "flowchart_examples")

    top_scoring = sorted(
        data.scoring_items,
        key=lambda req: float(re.search(r"分值：([0-9.]+)", req.text).group(1)) if re.search(r"分值：([0-9.]+)", req.text) else 0,
        reverse=True,
    )[:8]

    requests = [
        DiagramRequest(
            key="总体架构图",
            kind="architecture",
            output_stem="architecture",
            title="总体架构图",
            examples=architecture_examples,
            context={
                "project_name": data.project_name,
                "standards": data.standards[:20],
                "function_modules": [
                    {
                        "title": title,
                        "requirement_ids": [req.id for req in reqs],
                        "requirements": [requirement_payload(req, max_text=220) for req in reqs[:8]],
                    }
                    for title, reqs in grouped_functions.items()
                ],
                "performance_requirements": [requirement_payload(req) for req in data.performance_requirements[:12]],
                "quality_requirements": [requirement_payload(req) for req in data.quality_requirements[:18]],
                "business_requirements": [requirement_payload(req) for req in data.business_requirements[:10]],
                "scoring_items": [requirement_payload(req) for req in top_scoring],
            },
        )
    ]

    for index, (title, reqs) in enumerate(grouped_functions.items(), 1):
        related_scoring = [
            req
            for req in data.scoring_items
            if any(word in (req.title + req.text) for word in ["功能", "流程", "数据", "接口", "安全", "架构"])
        ][:8]
        stem = f"function-{index:03d}-{safe_filename(title)}"
        requests.append(
            DiagramRequest(
                key=title,
                kind="function_flow",
                output_stem=stem,
                title=f"{title}流程图",
                examples=flowchart_examples,
                context={
                    "project_name": data.project_name,
                    "function_title": title,
                    "function_requirements": [requirement_payload(req) for req in reqs],
                    "standards": data.standards[:12],
                    "performance_requirements": [requirement_payload(req, max_text=220) for req in data.performance_requirements[:8]],
                    "quality_requirements": [requirement_payload(req, max_text=220) for req in data.quality_requirements[:10]],
                    "related_scoring_items": [requirement_payload(req) for req in related_scoring],
                },
            )
        )

    return requests


def write_generated_mmd(path: Path, title: str, mermaid: str) -> None:
    path.write_text(f"%% 图名称：{title}\n{mermaid.strip()}\n", encoding="utf-8-sig")


def mermaid_node_labels(mermaid: str) -> list[str]:
    labels: list[str] = []
    patterns = [
        r"\[[\"']?([^][\"']{2,80})[\"']?\]",
        r"\([\"']?([^()\"']{2,80})[\"']?\)",
        r"\{[\"']?([^{}\"']{2,80})[\"']?\}",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, mermaid):
            label = clean_text(match)
            if label and label not in labels and not re.match(r"^[A-Za-z0-9_]+$", label):
                labels.append(label)
    return labels[:18]


def render_mermaid_png(mmd_path: Path, png_path: Path, title: str, mermaid: str) -> str:
    mmdc = which("mmdc")
    if mmdc:
        try:
            result = subprocess.run(
                [mmdc, "-i", str(mmd_path), "-o", str(png_path), "-b", "white"],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if result.returncode == 0 and png_path.exists():
                return "Mermaid CLI 原生渲染"
            render_error = (result.stderr or result.stdout or "").strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            render_error = str(exc)
    else:
        render_error = "未检测到 Mermaid CLI（mmdc）"

    labels = mermaid_node_labels(mermaid)
    if not labels:
        labels = ["模型已生成 Mermaid 源码", "请安装 mmdc 渲染原生图像"]
    draw_flow_png(png_path, title, labels)
    return f"Pillow 兼容渲染（非 Mermaid 原生渲染；{render_error[:180]}）"


def save_diagram_metadata(stem: str, spec: DiagramSpec, asset: DiagramAsset) -> None:
    payload = {
        "title": spec.title,
        "description": spec.description,
        "source_requirement_ids": spec.source_requirement_ids,
        "review_notes": spec.review_notes,
        "mmd_path": str(asset.mmd_path),
        "image_path": str(asset.image_path),
        "render_status": asset.render_status,
    }
    (RECORDS / f"{stem}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8-sig")


def create_diagrams(data: ProjectData) -> dict[str, DiagramAsset]:
    prompt_dir = RECORDS / "diagram-prompts"
    error_dir = RECORDS / "diagram-errors"
    reset_generated_files(prompt_dir, (".md",))
    reset_generated_files(error_dir, (".md",))

    requests = build_diagram_requests(data)
    for request in requests:
        system_prompt, user_prompt = build_prompts(request)
        save_prompt(prompt_dir / f"{request.output_stem}.md", system_prompt, user_prompt)

    try:
        client = OpenAICompatibleClient(OpenAICompatibleConfig.from_env())
    except LLMConfigError as exc:
        message = f"{exc}。已将全部图生成提示词写入 {prompt_dir}，未回退生成固定模板图。"
        (RECORDS / "diagram-generation-error.md").write_text(message + "\n", encoding="utf-8-sig")
        raise DiagramGenerationError(message) from exc

    diagrams: dict[str, DiagramAsset] = {}
    render_lines = ["# 图生成日志", ""]

    for request in requests:
        spec = generate_diagram_spec(client, request, prompt_dir=prompt_dir, error_dir=error_dir)
        mmd_path = RECORDS / f"{request.output_stem}.mmd"
        png_path = RECORDS / f"{request.output_stem}.png"
        write_generated_mmd(mmd_path, spec.title, spec.mermaid)
        render_status = render_mermaid_png(mmd_path, png_path, spec.title, spec.mermaid)
        asset = DiagramAsset(
            title=spec.title,
            mmd_path=mmd_path,
            image_path=png_path,
            description=spec.description,
            source_requirement_ids=spec.source_requirement_ids,
            review_notes=spec.review_notes,
            render_status=render_status,
        )
        diagrams[request.key] = asset
        save_diagram_metadata(request.output_stem, spec, asset)
        render_lines.append(f"- {spec.title}：{render_status}")

    (RECORDS / "diagram-generation-log.md").write_text("\n".join(render_lines) + "\n", encoding="utf-8-sig")
    return diagrams


def copy_blocks(data: ProjectData) -> dict[str, list[dict]]:
    return {
        "【COPY:项目名称】": [paragraph(data.project_name)],
        "【COPY:技术要求-功能要求】": prose_blocks(requirement_bullets(data.function_requirements)),
        "【COPY:技术要求-性能要求】": prose_blocks(requirement_bullets(data.performance_requirements)),
        "【COPY:技术要求-通用质量特性要求】": prose_blocks(requirement_bullets(data.quality_requirements)),
    }


def function_design_blocks(data: ProjectData, diagrams: dict[str, DiagramAsset]) -> list[dict]:
    blocks: list[dict] = []
    for title, reqs in group_by_title(data.function_requirements).items():
        blocks.append(heading(f"{title}功能", 3))
        req_text = "；".join(req.text for req in reqs[:4])
        blocks.extend(
            prose_blocks(
                f"""
                本功能围绕“{title}”相关招标要求展开设计，重点响应{req_text}等内容。系统将该类要求拆分为业务操作、数据处理、状态反馈、安全控制和运维记录等环节，确保功能设计能够与需求条款逐项对应。
                在实现方式上，系统通过前端交互、业务服务、数据访问、接口适配和日志审计等组件协同完成处理。用户发起操作后，系统先校验身份、权限、输入参数和当前业务状态，再调用对应业务规则完成数据查询、计算、更新或展示；处理结果同步形成可追踪记录。
                在异常和安全控制方面，系统对权限不足、输入不合法、接口中断、数据缺失和处理失败等情况给出明确提示，并记录操作日志。涉及关键业务参数、重要操作或安全风险的环节，应设置二次确认、告警提示、回滚处理和复核机制。
                """
            )
        )
        if title in diagrams:
            blocks.append(image(diagrams[title].image_path))
            blocks.append(caption(f"{title}流程图"))
        rows = [["序号", "需求摘录", "响应方式"]]
        for idx, req in enumerate(reqs, 1):
            rows.append([str(idx), req.text, "纳入功能设计、流程控制、数据处理和异常处理"])
        blocks.append(table(rows))
    return blocks


def performance_blocks(data: ProjectData) -> list[dict]:
    blocks = [paragraph("性能设计围绕招标文件中的响应时间、初始化时间、查询汇总效率、指令响应率和服务稳定性等指标展开。系统采用分层处理、缓存优化、异步任务、索引优化、状态监测和异常降级等方式保障关键场景的性能表现。")]
    rows = [["序号", "性能要求", "设计响应"]]
    for idx, req in enumerate(data.performance_requirements, 1):
        rows.append([str(idx), req.text, "通过缓存、异步处理、索引优化、资源监控和超时提示机制响应"])
    blocks.append(table(rows))
    return blocks


def quality_blocks(data: ProjectData, quality_name: str | None = None) -> list[dict]:
    reqs = data.quality_requirements
    if quality_name:
        reqs = [req for req in reqs if quality_name in req.title]
    if not reqs:
        return [paragraph("该部分根据招标文件质量特性要求进行设计，具体指标和证明材料需结合投标人实际能力复核。")]
    intro = f"{quality_name or '通用质量特性'}设计围绕招标文件提出的相关质量要求展开，重点覆盖" + "、".join(sorted({req.title for req in reqs})[:6]) + "等方面。"
    rows = [["序号", "质量要求", "设计措施"]]
    for idx, req in enumerate(reqs, 1):
        rows.append([str(idx), req.text, "通过过程控制、接口约束、日志审计、测试验证和运维保障落实"])
    return [paragraph(intro), table(rows)]


def gen_blocks(data: ProjectData, diagrams: dict[str, DiagramAsset]) -> dict[str, list[dict]]:
    top_scoring = sorted(data.scoring_items, key=lambda req: float(re.search(r"分值：([0-9.]+)", req.text).group(1)) if re.search(r"分值：([0-9.]+)", req.text) else 0, reverse=True)[:5]
    function_titles = list(group_by_title(data.function_requirements).keys())
    business_text = "；".join(req.text for req in data.business_requirements[:5])
    scoring_text = "；".join(req.text for req in top_scoring[:3])

    return {
        "【GEN:编写目的】": prose_blocks(
            f"本设计方案用于响应{data.project_name}项目的技术要求、商务要求和技术评分要求，说明投标方案的建设思路、功能设计、架构设计、质量保障、实施交付和服务承诺安排。文档以 input 目录中的招标资料为事实来源，通过需求响应矩阵建立条款与方案章节之间的对应关系，避免脱离依据的泛化描述。"
        ),
        "【GEN:建设内容】": prose_blocks(
            f"本项目建设内容主要覆盖{data.project_name}相关软件能力建设，功能范围包括{ '、'.join(function_titles[:8]) }等模块，并结合性能、质量、安全、接口、部署、培训和售后等要求形成完整技术方案。商务侧重点包括{business_text}。"
        ),
        "【GEN:总体架构设计】": prose_blocks(
            f"系统总体架构采用分层设计思路，围绕用户访问、前端展现、业务应用、数据资源、接口集成、安全保障和运维支撑进行组织。功能模块依据招标要求拆分为{ '、'.join(function_titles[:8]) }等能力，保证业务功能、数据处理和接口协同之间边界清晰。\n"
            f"架构设计同时响应评分表关注的重点：{scoring_text}。在实现上，方案强调业务架构、逻辑架构、技术架构和数据架构之间的一致性，通过标准接口、模块化组件、统一日志和配置管理提升扩展性与可维护性。"
        ),
        "【GEN:总体架构图】": [image(diagrams["总体架构图"].image_path), caption("总体架构图")],
        "【GEN:架构图说明】": prose_blocks(diagrams["总体架构图"].description),
        "【GEN:设计原则】": prose_blocks("方案遵循需求可追踪、架构分层、模块解耦、标准适配、安全可控、稳定可靠、易维护和可扩展原则。每项设计均应能够追溯到技术要求、商务要求或评分项；对人员、资质、承诺、案例等缺少事实依据的内容，保留确认或复核项。"),
        "【GEN:部署架构设计】": prose_blocks("部署架构根据招标文件中的运行环境、接口接入、安全防护和运维保障要求进行设计。系统应支持业务服务、数据存储、接口适配和运维监控的分层部署，保留按现场设备、网络边界和安全策略调整的能力；未在 input 中明确的硬件型号、厂商品牌和资源规格不在初稿中擅自承诺。"),
        "【GEN:功能设计总述】": prose_blocks(f"功能设计以技术要求中抽取的 {len(data.function_requirements)} 条功能要求为依据，按照业务主题聚合为 {len(function_titles)} 个功能模块。每个模块均从功能目标、建设内容、实现方式、业务流程、数据处理、权限安全、异常处理和招标响应关系展开。"),
        "【GEN:功能设计章节】": function_design_blocks(data, diagrams),
        "【GEN:性能设计章节】": performance_blocks(data),
        "【GEN:数据库设计总述】": prose_blocks("数据库设计围绕业务数据、配置数据、日志数据、接口数据、交付资料和备份数据建立分类管理思路。数据模型应服务于功能实现、性能响应、质量追溯和验收交付，具体字段长度、编码和约束可在详细设计阶段继续细化。"),
        "【GEN:数据库架构设计】": prose_blocks("数据架构建议采用业务库、日志库、配置库和备份库分区管理。业务库保存核心业务对象及状态，日志库保存操作、告警和接口记录，配置库保存参数、角色、权限和环境配置，备份库保存归档和恢复数据。"),
        "【GEN:核心业务数据设计】": prose_blocks("核心业务数据来自技术要求中的功能模块，包括业务对象、操作记录、接口报文、状态数据、告警事件、配置参数、用户权限和交付资料等。所有关键数据应保留来源、版本、时间、责任主体和处理状态，便于追溯和复核。"),
        "【GEN:数据库表设计】": [table([["表名", "主要字段", "用途", "备注"], ["requirement_item", "id、source_file、category、title、text、target_section、status", "保存需求条款", "由 input 自动抽取"], ["generation_block", "placeholder、block_type、content、source_ids", "保存生成内容块", "用于 Word 填充和追溯"], ["review_item", "id、type、content、status", "保存确认和复核事项", "需人工确认"]])],
        "【GEN:通用质量特性设计总述】": quality_blocks(data),
        "【GEN:可靠性设计】": quality_blocks(data, "可靠性"),
        "【GEN:维修性设计】": quality_blocks(data, "维修性"),
        "【GEN:保障性设计】": quality_blocks(data, "保障性"),
        "【GEN:测试性设计】": quality_blocks(data, "测试性"),
        "【GEN:安全性设计】": quality_blocks(data, "安全性"),
        "【GEN:环境适应性设计】": quality_blocks(data, "环境适应性"),
        "【GEN:关键技术】": prose_blocks("关键技术围绕需求抽取、需求矩阵、模板占位符识别、分段内容生成、Word 样式填充、图表生成、质量检查和记录追溯展开。方案生成过程应保证输入来源明确、生成内容可解释、人工确认项不被编造、输出文档格式受模板样式约束。"),
        "【GEN:质量控制总述】": prose_blocks("质量控制覆盖输入解析、条款分类、矩阵映射、内容生成、模板填充、记录输出和人工复核全过程。每个占位符的处理结果写入日志，所有 CONFIRM 和 REVIEW 项进入清单，便于后续补充资料和审稿。"),
        "【GEN:风险评估与控制】": [table([["风险", "表现", "影响", "控制措施"], ["输入解析风险", "条款识别不完整", "需求遗漏", "保留原文、输出矩阵、人工复核"], ["生成偏差风险", "正文与条款不匹配", "响应不足", "按占位符绑定来源和评分项"], ["承诺风险", "人员资质或服务承诺缺依据", "投标合规风险", "使用 CONFIRM/REVIEW 保留复核"], ["格式风险", "模板填充后样式不一致", "文档质量下降", "以 Word 样式为主，必要时做 PDF 抽检"]])],
        "【GEN:质量保证措施】": prose_blocks("质量保证措施包括需求抽取复核、矩阵覆盖检查、生成内容审阅、模板占位符检查、人工确认项管理和最终输出检查。对涉及资质、人员、业绩、价格、工期和承诺的内容，不以自动生成结果作为最终结论。"),
        "【GEN:培训方案】": prose_blocks("培训方案依据商务要求生成，内容覆盖系统总体介绍、功能操作、权限与安全、数据维护、常见问题处理、交付资料说明和验收配合。培训对象、时间、地点、次数、讲师和考核方式应结合投标人实际安排及用户要求最终确认。"),
    }


def review_blocks(data: ProjectData) -> dict[str, list[dict]]:
    delivery = data.delivery_rows if data.delivery_rows else [["序号", "交付物", "数量", "说明"]]
    return {
        "【REVIEW:质量体系与资质响应说明】": prose_blocks("质量体系、资质证书、软件开发过程证明和相关证明材料需结合投标人真实资料填写。本初稿仅提示应覆盖质量体系、过程管理、测试验证和交付质量控制等内容。"),
        "【REVIEW:服务质量保障措施】": prose_blocks("服务质量保障可根据商务要求形成初稿，但服务团队、联系人、响应时限、到场方式、费用边界和升级服务承诺均需人工复核。"),
        "【REVIEW:项目进度计划】": prose_blocks("项目进度计划建议按启动准备、需求确认、设计开发、测试联调、部署上线、试运行、验收交付和运维支持组织。具体日期、里程碑和资源投入需结合合同与进场安排复核。"),
        "【REVIEW:成果交付及验收】": prose_blocks("成果交付及验收应以技术要求、商务要求和合同条款为准。验收重点包括功能覆盖、性能指标、接口联调、文档完整性、问题整改闭环和现场部署运行情况。"),
        "【REVIEW:交付物清单】": [paragraph("交付物清单由技术要求中的交付表提取，数量、介质、签收要求和最终版本需人工复核。"), table(delivery)],
        "【REVIEW:应急支援保障承诺】": prose_blocks("应急支援保障承诺需结合投标人服务能力、驻场安排、备件工具、问题分级和费用边界进行复核，避免生成超出实际能力的强承诺。"),
        "【REVIEW:定期跟踪服务承诺】": prose_blocks("定期跟踪服务可包括巡检、运行状态回访、问题统计、版本维护建议、培训补强和服务记录归档，具体频次、方式和报告格式需人工确认。"),
    }


def all_blocks(data: ProjectData, diagrams: dict[str, DiagramAsset]) -> dict[str, list[dict]]:
    blocks = {}
    blocks.update(copy_blocks(data))
    blocks.update(gen_blocks(data, diagrams))
    blocks.update(review_blocks(data))
    return blocks


def write_intermediate_data(data: ProjectData) -> None:
    (RECORDS / "requirements.json").write_text(json.dumps(asdict(data), ensure_ascii=False, indent=2), encoding="utf-8-sig")

    rows = [
        "# 需求响应矩阵",
        "",
        "| 编号 | 来源文件 | 类型 | 主题 | 原文摘录 | 关键词 | 对应章节 | 是否生成图 | 状态 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for req in data.function_requirements + data.performance_requirements + data.quality_requirements + data.business_requirements + data.scoring_items:
        rows.append(
            f"| {req.id} | {req.source_file} | {req.category} | {req.title} | {req.text} | {'、'.join(req.keywords)} | {req.target_section} | {'是' if req.need_diagram else '否'} | {req.status} |"
        )
    (RECORDS / "requirements-matrix.md").write_text("\n".join(rows) + "\n", encoding="utf-8-sig")

    confirm_items = [
        "投标人名称",
        "项目团队人员说明",
        "项目团队-职务分工",
        "项目团队-姓名",
        "项目团队-职称",
        "项目团队-专业",
        "项目团队-从业资格",
        "项目团队-相关工作年限",
        "质量保证期承诺",
        "售后服务响应承诺",
    ]
    (RECORDS / "人工确认清单.md").write_text("# 人工确认清单\n\n" + "\n".join(f"- {item}" for item in confirm_items) + "\n", encoding="utf-8-sig")

    review_items = [
        "质量体系与资质响应说明",
        "服务质量保障措施",
        "项目进度计划",
        "成果交付及验收",
        "交付物清单",
        "应急支援保障承诺",
        "定期跟踪服务承诺",
    ]
    (RECORDS / "复核清单.md").write_text("# 复核清单\n\n" + "\n".join(f"- {item}" for item in review_items) + "\n", encoding="utf-8-sig")
    (RECORDS / "missing-source.md").write_text("# 未找到来源事项\n\n未匹配到来源的 COPY 占位符会在占位符日志中标记。CONFIRM 和 REVIEW 事项见对应清单。\n", encoding="utf-8-sig")


def para_after(paragraph, text: str = ""):
    new_p = OxmlElement("w:p")
    paragraph._p.addnext(new_p)
    new_para = paragraph.__class__(new_p, paragraph._parent)
    if text:
        new_para.add_run(text)
    return new_para


def set_para_text(paragraph, text: str, bold: bool = False) -> None:
    for run in paragraph.runs:
        run.text = ""
    run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
    run.text = text
    run.bold = bold
    run.font.name = "仿宋"
    run.font.size = Pt(12)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋")
    paragraph.paragraph_format.line_spacing = 1.25
    if text and not bold:
        paragraph.paragraph_format.first_line_indent = Pt(24)


def style_heading(paragraph, level: int = 3) -> None:
    for run in paragraph.runs:
        run.bold = True
        run.font.name = "黑体"
        run.font.size = Pt(14 if level <= 2 else 13)
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "黑体")
    paragraph.paragraph_format.space_before = Pt(6)
    paragraph.paragraph_format.space_after = Pt(3)


def add_block_after(anchor, block: dict):
    if block["type"] == "paragraph":
        p = para_after(anchor, block["text"])
        set_para_text(p, block["text"])
        return p
    if block["type"] == "heading":
        p = para_after(anchor, block["text"])
        set_para_text(p, block["text"], bold=True)
        style_heading(p, block.get("level", 3))
        return p
    if block["type"] == "image":
        p = para_after(anchor)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(block["path"], width=Inches(block.get("width", 5.8)))
        return p
    if block["type"] == "caption":
        p = para_after(anchor, block["text"])
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        set_para_text(p, block["text"])
        p.paragraph_format.first_line_indent = None
        return p
    if block["type"] == "table":
        doc = anchor.part.document
        rows = block["rows"]
        table_obj = doc.add_table(rows=len(rows), cols=max(len(row) for row in rows))
        table_obj.style = "Table Grid"
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                cell = table_obj.cell(i, j)
                cell.text = str(value)
                for p in cell.paragraphs:
                    for run in p.runs:
                        run.font.name = "仿宋"
                        run.font.size = Pt(9 if len(str(value)) > 35 else 10.5)
                        run._element.rPr.rFonts.set(qn("w:eastAsia"), "仿宋")
        anchor._p.addnext(table_obj._tbl)
        new_p = OxmlElement("w:p")
        table_obj._tbl.addnext(new_p)
        return Paragraph(new_p, anchor._parent)
    raise ValueError(block["type"])


def replace_placeholder_paragraph(doc: Document, placeholder: str, blocks: list[dict]) -> str:
    for paragraph_obj in doc.paragraphs:
        if paragraph_obj.text.strip() == placeholder:
            first = blocks[0] if blocks else paragraph("")
            if first["type"] == "paragraph":
                set_para_text(paragraph_obj, first["text"])
                anchor = paragraph_obj
            elif first["type"] == "heading":
                set_para_text(paragraph_obj, first["text"], bold=True)
                style_heading(paragraph_obj, first.get("level", 3))
                anchor = paragraph_obj
            else:
                set_para_text(paragraph_obj, "")
                paragraph_obj.paragraph_format.first_line_indent = None
                anchor = add_block_after(paragraph_obj, first)
            for block in blocks[1:]:
                anchor = add_block_after(anchor, block)
            return "已生成"

    for table_obj in doc.tables:
        for row in table_obj.rows:
            for cell in row.cells:
                if cell.text.strip() == placeholder:
                    text = "\n".join(block.get("text", "") for block in blocks if block["type"] in {"paragraph", "heading", "caption"})
                    cell.text = text or placeholder
                    return "已填入表格" if text else "保留"
    return "未找到"


def replace_inline(doc: Document, placeholder: str, value: str) -> str:
    status = "未找到"
    for paragraph_obj in doc.paragraphs:
        if placeholder in paragraph_obj.text:
            set_para_text(paragraph_obj, paragraph_obj.text.replace(placeholder, value))
            status = "已替换"
    for table_obj in doc.tables:
        for row in table_obj.rows:
            for cell in row.cells:
                if placeholder in cell.text:
                    cell.text = cell.text.replace(placeholder, value)
                    status = "已替换"
    return status


def patch_docx_xml_text(docx_path: Path, replacements: dict[str, str]) -> None:
    tmp_path = docx_path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(docx_path, "r") as zin, zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data_bytes = zin.read(item.filename)
            if item.filename.endswith(".xml"):
                text = data_bytes.decode("utf-8")
                for old, new in replacements.items():
                    text = text.replace(old, new)
                data_bytes = text.encode("utf-8")
            zout.writestr(item, data_bytes)
    tmp_path.replace(docx_path)


def write_placeholder_log(log: list[tuple[str, str]], data: ProjectData) -> None:
    lines = ["# 占位符填充日志", "", "| 占位符 | 处理结果 |", "|---|---|"]
    for placeholder, status in log:
        lines.append(f"| {placeholder} | {status} |")
    (RECORDS / "placeholder-fill-log.md").write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    coverage = f"""# 覆盖检查报告

## 输入解析

- 项目名称：{data.project_name}
- 功能要求：{len(data.function_requirements)} 条
- 性能要求：{len(data.performance_requirements)} 条
- 质量要求：{len(data.quality_requirements)} 条
- 商务要求：{len(data.business_requirements)} 条
- 评分项：{len(data.scoring_items)} 条

## 生成说明

本次生成先将 input 中 Word 文档抽取为 requirements.json，再生成 requirements-matrix.md，随后按模板占位符生成内容块并填入 Word。

## 复核说明

人员、资质、证书、业绩、承诺和最终交付验收条款仍需人工确认或复核，详见人工确认清单和复核清单。
"""
    (RECORDS / "coverage-check.md").write_text(coverage, encoding="utf-8-sig")


def main() -> None:
    ensure_dirs()
    data = load_project_data()
    write_intermediate_data(data)
    diagrams = create_diagrams(data)
    blocks = all_blocks(data, diagrams)

    out_docx = OUTPUT / f"{data.project_name}设计方案_V1.00_{TODAY}.docx"
    copy2(TEMPLATE, out_docx)
    doc = Document(str(out_docx))
    log: list[tuple[str, str]] = []

    log.append(("【COPY:项目名称】", replace_inline(doc, "【COPY:项目名称】", data.project_name)))
    for placeholder, placeholder_blocks in blocks.items():
        if placeholder == "【COPY:项目名称】":
            continue
        log.append((placeholder, replace_placeholder_paragraph(doc, placeholder, placeholder_blocks)))

    all_doc_text = "\n".join(
        [p.text for p in doc.paragraphs]
        + [cell.text for table_obj in doc.tables for row in table_obj.rows for cell in row.cells]
    )
    confirm_placeholders = sorted(set(re.findall(r"【CONFIRM:[^】]+】", all_doc_text)))
    for placeholder in confirm_placeholders:
        log.append((placeholder, "按规则保留，需人工确认"))

    doc.save(str(out_docx))
    patch_docx_xml_text(out_docx, {"北京瑞晟成科技发展有限公司": "投标人名称待确认"})
    write_placeholder_log(log, data)
    print(out_docx)


if __name__ == "__main__":
    main()
