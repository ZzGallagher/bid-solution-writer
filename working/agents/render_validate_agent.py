#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Render Validate Agent.

This stage consumes Mermaid Agent output, renders diagram PNG files when a
Mermaid renderer is available, records failures, and publishes
``diagram-manifest.json`` for Review Gate and Word assembly.

The native renderer is intentionally external. By default the agent looks for
``mmdc`` on PATH; callers can pass ``--renderer-command`` to point at a Mermaid
CLI-compatible executable.
"""

from __future__ import annotations

import argparse
import binascii
import json
import re
import shutil
import struct
import subprocess
import sys
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


AGENT_NAME = "Render Validate Agent"
AGENT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"

DIAGRAM_ID_RE = re.compile(r"^DG[0-9]{3}$")
REQ_ID_RE = re.compile(r"^(T|P|Q|B|D)[0-9]{3}$")
FLOWCHART_RE = re.compile(r"^\s*flowchart\s+(TB|TD)\b", re.IGNORECASE)
ALLOWED_HEADER_RE = re.compile(
    r"^\s*(flowchart\s+(TB|TD)|sequenceDiagram|classDiagram|stateDiagram-v2|erDiagram|journey|gantt|pie|gitGraph|mindmap)\b",
    re.IGNORECASE,
)
ALLOWED_KINDS = {
    "architecture",
    "business_flow",
    "data_flow",
    "function_flow",
    "deployment",
    "security",
    "sequence",
    "other",
}


@dataclass
class ImageCheck:
    exists: bool
    blank_risk: str
    width: int = 0
    height: int = 0
    file_size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "width": self.width,
            "height": self.height,
            "file_size_bytes": self.file_size_bytes,
            "blank_risk": self.blank_risk,
        }


@dataclass
class RenderOutcome:
    diagram_id: str
    render_status: str
    image_path: Path | None
    image_check: ImageCheck
    errors: list[dict[str, Any]] = field(default_factory=list)
    review_notes: list[str] = field(default_factory=list)
    assembly_allowed: bool = False


class RenderValidateAgent:
    def __init__(
        self,
        workspace: Path,
        records_dir: Path,
        output_dir: Path,
        renderer_command: str | None = None,
        timeout_seconds: int = 60,
        disable_fallback: bool = False,
    ) -> None:
        self.workspace = workspace
        self.records_dir = records_dir
        self.output_dir = output_dir
        self.renderer_command = renderer_command
        self.timeout_seconds = timeout_seconds
        self.disable_fallback = disable_fallback
        now = datetime.now().astimezone()
        self.generated_at = now.isoformat(timespec="seconds")
        self.run_id = f"RUN-{now:%Y%m%d-%H%M%S}"
        self.log_events: list[dict[str, Any]] = []

    def run(self) -> dict[str, Path]:
        specs_path = self.records_dir / "diagram-specs.json"
        specs = self.load_json(specs_path)
        self.run_id = str(specs.get("run_id") or self.run_id)
        self.validate_specs(specs)

        staging_dir = self.workspace / "working" / "agent-system" / "staging" / "diagrams" / self.run_id
        published_dir = self.workspace / "working" / "agent-system" / "published" / "diagrams" / self.run_id
        staging_image_dir = staging_dir / "diagrams"
        published_image_dir = published_dir / "diagrams"
        output_image_dir = self.output_dir / "diagrams"
        for directory in (staging_dir, published_dir, staging_image_dir, published_image_dir, self.output_dir, output_image_dir):
            directory.mkdir(parents=True, exist_ok=True)

        renderer = self.resolve_renderer()
        manifest_diagrams = []
        outcomes: list[RenderOutcome] = []
        for diagram in specs["diagrams"]:
            source_text = self.load_mermaid_source(diagram)
            mmd_path = staging_dir / f"{diagram['diagram_id']}.mmd"
            mmd_path.write_text(source_text.rstrip() + "\n", encoding="utf-8")
            target_png = staging_image_dir / f"{diagram['diagram_id']}.png"
            outcome = self.render_diagram(diagram, source_text, mmd_path, target_png, renderer)
            outcomes.append(outcome)

            output_png = output_image_dir / f"{diagram['diagram_id']}.png" if outcome.image_path else None
            if outcome.image_path and outcome.image_path.exists():
                shutil.copy2(outcome.image_path, published_image_dir / outcome.image_path.name)
                shutil.copy2(outcome.image_path, output_png)
                final_check = self.check_image(output_png)
            else:
                final_check = outcome.image_check

            manifest_diagrams.append(
                {
                    "diagram_id": diagram["diagram_id"],
                    "title": diagram["title"],
                    "kind": diagram["kind"],
                    "mermaid_path": diagram["mermaid_path"],
                    "image_path": self.relative(output_png) if output_png else "",
                    "source_requirement_ids": diagram["source_requirement_ids"],
                    "description": diagram["description"],
                    "render_status": outcome.render_status,
                    "assembly_allowed": outcome.assembly_allowed and final_check.exists and final_check.blank_risk != "high",
                    "image_check": final_check.to_dict(),
                    "errors": outcome.errors,
                    "review_notes": self.unique(diagram.get("review_notes", []) + outcome.review_notes),
                }
            )

        manifest = self.build_manifest(specs, specs_path, manifest_diagrams, renderer)
        self.validate_manifest(manifest)

        outputs = {
            "diagram-manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            "diagram-render-log.md": self.render_log(manifest, outcomes, renderer),
        }
        for name, content in outputs.items():
            (staging_dir / name).write_text(content, encoding="utf-8")
        for name in outputs:
            shutil.copy2(staging_dir / name, published_dir / name)
            shutil.copy2(staging_dir / name, self.output_dir / name)

        return {"staging_dir": staging_dir, "published_dir": published_dir, "output_dir": self.output_dir}

    def render_diagram(
        self,
        diagram: dict[str, Any],
        source_text: str,
        mmd_path: Path,
        target_png: Path,
        renderer: str | None,
    ) -> RenderOutcome:
        diagram_id = diagram["diagram_id"]
        syntax_errors = self.syntax_errors(diagram, source_text)
        if syntax_errors:
            errors = [
                {
                    "message": message,
                    "recoverable": True,
                    "suggested_action": "回到 Mermaid Agent 修正 Mermaid 源码后重新渲染。",
                }
                for message in syntax_errors
            ]
            self.add_log(diagram_id, "failed", "Mermaid syntax validation failed.", errors)
            return RenderOutcome(diagram_id, "failed", None, ImageCheck(False, "high"), errors, [], False)

        if diagram.get("status") == "blocked":
            error = {
                "message": "diagram-specs.json 将该图标记为 blocked，跳过渲染。",
                "recoverable": True,
                "suggested_action": "先修复 Mermaid Agent 输出的 blocked 图表。",
            }
            self.add_log(diagram_id, "skipped", error["message"], [error])
            return RenderOutcome(diagram_id, "skipped", None, ImageCheck(False, "high"), [error], [], False)

        if renderer:
            native = self.try_native_render(diagram_id, renderer, mmd_path, target_png)
            if native:
                return native

        if self.disable_fallback:
            error = {
                "message": "原生 Mermaid 渲染失败，且已禁用降级渲染。",
                "recoverable": True,
                "suggested_action": "安装 Mermaid CLI 或取消 --disable-fallback 后重新运行。",
            }
            self.add_log(diagram_id, "failed", error["message"], [error])
            return RenderOutcome(diagram_id, "failed", None, ImageCheck(False, "high"), [error], [], False)

        fallback_errors: list[dict[str, Any]] = []
        if not renderer:
            fallback_errors.append(
                {
                    "message": "未找到 Mermaid CLI 渲染器，已生成降级 PNG。",
                    "recoverable": True,
                    "suggested_action": "安装 @mermaid-js/mermaid-cli 或通过 --renderer-command 指定渲染器以获得原生图。",
                }
            )
        self.write_fallback_png(target_png, diagram, source_text)
        image_check = self.check_image(target_png)
        if not image_check.exists or image_check.blank_risk == "high":
            error = {
                "message": "降级 PNG 生成后未通过图片检查。",
                "recoverable": True,
                "suggested_action": "检查输出目录权限，或安装 Mermaid CLI 后重新原生渲染。",
            }
            fallback_errors.append(error)
            self.add_log(diagram_id, "failed", error["message"], fallback_errors)
            return RenderOutcome(diagram_id, "failed", None, image_check, fallback_errors, [], False)

        review_notes = ["render_status=fallback_rendered：原生 Mermaid 渲染不可用或失败，已生成降级 PNG，需人工关注。"]
        self.add_log(diagram_id, "fallback_rendered", "Fallback PNG generated and explicitly marked.", fallback_errors)
        return RenderOutcome(diagram_id, "fallback_rendered", target_png, image_check, fallback_errors, review_notes, True)

    def try_native_render(self, diagram_id: str, renderer: str, mmd_path: Path, target_png: Path) -> RenderOutcome | None:
        command = [renderer, "-i", str(mmd_path), "-o", str(target_png)]
        try:
            completed = subprocess.run(
                command,
                cwd=self.workspace,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            error = {
                "message": f"Mermaid CLI 渲染超时：{exc}",
                "recoverable": True,
                "suggested_action": "简化 Mermaid 图或提高 --timeout-seconds 后重试。",
            }
            self.add_log(diagram_id, "native_failed", error["message"], [error])
            return None
        except OSError as exc:
            error = {
                "message": f"Mermaid CLI 无法启动：{exc}",
                "recoverable": True,
                "suggested_action": "检查 --renderer-command 是否指向可执行文件。",
            }
            self.add_log(diagram_id, "native_failed", error["message"], [error])
            return None

        image_check = self.check_image(target_png)
        if completed.returncode == 0 and image_check.exists and image_check.blank_risk != "high":
            self.add_log(diagram_id, "native_rendered", "Mermaid CLI rendered PNG successfully.", [])
            return RenderOutcome(diagram_id, "native_rendered", target_png, image_check, [], [], True)

        stderr = self.shorten((completed.stderr or completed.stdout or "").strip(), 600)
        message = stderr or f"Mermaid CLI exited with code {completed.returncode}."
        error = {
            "message": message,
            "recoverable": True,
            "suggested_action": "查看 diagram-render-log.md，修复 Mermaid 源码或渲染器环境后重试。",
        }
        self.add_log(diagram_id, "native_failed", message, [error])
        return None

    def resolve_renderer(self) -> str | None:
        if self.renderer_command:
            path = Path(self.renderer_command)
            if path.exists():
                return str(path)
            return shutil.which(self.renderer_command)
        return shutil.which("mmdc")

    def load_mermaid_source(self, diagram: dict[str, Any]) -> str:
        mermaid_path = self.path_from_workspace(diagram["mermaid_path"])
        if mermaid_path.exists():
            return mermaid_path.read_text(encoding="utf-8", errors="replace")
        source = str(diagram.get("mermaid", "")).strip()
        if source:
            self.add_log(diagram["diagram_id"], "source_warning", f"Mermaid 文件不存在，使用 diagram-specs.json 内联 mermaid 字段：{diagram['mermaid_path']}", [])
            return source
        raise FileNotFoundError(f"{diagram['diagram_id']} 缺少 Mermaid 源码文件且 diagram-specs.json 未提供 mermaid 字段：{diagram['mermaid_path']}")

    def syntax_errors(self, diagram: dict[str, Any], source_text: str) -> list[str]:
        errors = []
        source = source_text.strip()
        if not source:
            errors.append("Mermaid 源码为空。")
            return errors
        if not ALLOWED_HEADER_RE.match(source):
            errors.append("Mermaid 首行不是允许的图类型。")
        if diagram.get("kind") != "sequence" and not FLOWCHART_RE.match(source):
            errors.append("非 sequence 图默认必须使用 flowchart TB 或 flowchart TD。")
        if "```" in source:
            errors.append("Mermaid 源码中不应包含 Markdown 代码围栏。")
        opens = source.count("[") + source.count("(") + source.count("{")
        closes = source.count("]") + source.count(")") + source.count("}")
        if opens != closes:
            errors.append("Mermaid 源码括号数量不平衡。")
        return errors

    def build_manifest(
        self,
        specs: dict[str, Any],
        specs_path: Path,
        diagrams: list[dict[str, Any]],
        renderer: str | None,
    ) -> dict[str, Any]:
        summary = {
            "total": len(diagrams),
            "native_rendered": sum(1 for item in diagrams if item["render_status"] == "native_rendered"),
            "fallback_rendered": sum(1 for item in diagrams if item["render_status"] == "fallback_rendered"),
            "failed": sum(1 for item in diagrams if item["render_status"] == "failed"),
            "blocked": sum(1 for item in diagrams if item["assembly_allowed"] is not True),
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "artifact": "diagram-manifest",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": {
                "agent": AGENT_NAME,
                "version": AGENT_VERSION,
                "renderer": renderer or "fallback-png",
            },
            "inputs": [
                {
                    "artifact": "diagram-specs",
                    "path": self.relative(specs_path),
                    "schema_version": str(specs.get("schema_version", "unknown")),
                }
            ],
            "diagrams": diagrams,
            "render_summary": summary,
        }

    def validate_specs(self, specs: dict[str, Any]) -> None:
        self.require_fields(specs, ["schema_version", "artifact", "run_id", "generated_at", "producer", "inputs", "diagrams"], "diagram-specs.json")
        if specs["schema_version"] != SCHEMA_VERSION or specs["artifact"] != "diagram-specs":
            raise RuntimeError("diagram-specs.json artifact 或 schema_version 不符合契约。")
        if not specs["diagrams"]:
            raise RuntimeError("diagram-specs.json 至少需要包含一张图。")
        diagram_ids = [item.get("diagram_id") for item in specs["diagrams"]]
        if len(diagram_ids) != len(set(diagram_ids)):
            raise RuntimeError("diagram-specs.json diagram_id 存在重复。")
        for diagram in specs["diagrams"]:
            self.require_fields(
                diagram,
                ["diagram_id", "title", "kind", "mermaid_path", "mermaid", "source_requirement_ids", "description", "status"],
                f"diagram-specs.json#{diagram.get('diagram_id', '?')}",
            )
            if not DIAGRAM_ID_RE.match(str(diagram["diagram_id"])):
                raise RuntimeError(f"diagram_id 格式不正确：{diagram['diagram_id']}")
            if diagram["kind"] not in ALLOWED_KINDS:
                raise RuntimeError(f"{diagram['diagram_id']} kind 不在允许范围内：{diagram['kind']}")
            source_ids = diagram.get("source_requirement_ids", [])
            if not source_ids or any(not REQ_ID_RE.match(str(req_id)) for req_id in source_ids):
                raise RuntimeError(f"{diagram['diagram_id']} source_requirement_ids 缺失或格式不正确。")

    def validate_manifest(self, manifest: dict[str, Any]) -> None:
        self.require_fields(manifest, ["schema_version", "artifact", "run_id", "generated_at", "producer", "inputs", "diagrams", "render_summary"], "diagram-manifest.json")
        if manifest["schema_version"] != SCHEMA_VERSION or manifest["artifact"] != "diagram-manifest":
            raise RuntimeError("diagram-manifest.json artifact 或 schema_version 不符合契约。")
        for diagram in manifest["diagrams"]:
            self.require_fields(
                diagram,
                ["diagram_id", "title", "kind", "mermaid_path", "image_path", "source_requirement_ids", "description", "render_status", "assembly_allowed"],
                f"diagram-manifest.json#{diagram.get('diagram_id', '?')}",
            )
            if diagram["render_status"] == "fallback_rendered":
                notes = diagram.get("review_notes", []) + [error.get("message", "") for error in diagram.get("errors", []) if isinstance(error, dict)]
                if not notes:
                    raise RuntimeError(f"{diagram['diagram_id']} fallback_rendered 缺少降级说明。")
            if diagram["render_status"] == "native_rendered" and not diagram.get("image_path"):
                raise RuntimeError(f"{diagram['diagram_id']} native_rendered 必须包含 image_path。")
        summary = manifest["render_summary"]
        counted = summary["native_rendered"] + summary["fallback_rendered"] + summary["failed"]
        skipped = sum(1 for item in manifest["diagrams"] if item["render_status"] == "skipped")
        if counted + skipped != summary["total"]:
            raise RuntimeError("diagram-manifest.json render_summary 计数不一致。")

    def render_log(self, manifest: dict[str, Any], outcomes: list[RenderOutcome], renderer: str | None) -> str:
        lines = [
            "# Diagram Render Log",
            "",
            f"- Run ID: `{manifest['run_id']}`",
            f"- Generated At: `{manifest['generated_at']}`",
            f"- Renderer: `{renderer or 'fallback-png'}`",
            "",
            "## Summary",
            "",
            "| Total | Native | Fallback | Failed | Blocked |",
            "|---:|---:|---:|---:|---:|",
            "| {total} | {native_rendered} | {fallback_rendered} | {failed} | {blocked} |".format(**manifest["render_summary"]),
            "",
            "## Diagrams",
            "",
            "| Diagram | Status | Assembly | Image | Notes |",
            "|---|---|---|---|---|",
        ]
        manifest_by_id = {item["diagram_id"]: item for item in manifest["diagrams"]}
        for outcome in outcomes:
            item = manifest_by_id[outcome.diagram_id]
            notes = "; ".join(item.get("review_notes", []) + [error.get("message", "") for error in item.get("errors", [])])
            lines.append(
                f"| {outcome.diagram_id} | {item['render_status']} | {str(item['assembly_allowed']).lower()} | `{item['image_path'] or '-'}` | {self.escape_md(notes or '-')} |"
            )
        lines.extend(["", "## Events", ""])
        if not self.log_events:
            lines.extend(["无额外事件。", ""])
            return "\n".join(lines)
        lines.extend(["| Diagram | Stage | Message |", "|---|---|---|"])
        for event in self.log_events:
            lines.append(f"| {event['diagram_id']} | {event['stage']} | {self.escape_md(event['message'])} |")
        lines.append("")
        return "\n".join(lines)

    def check_image(self, path: Path | None) -> ImageCheck:
        if path is None or not path.exists():
            return ImageCheck(False, "high")
        size = path.stat().st_size
        if size <= 0:
            return ImageCheck(False, "high", file_size_bytes=size)
        try:
            width, height, blank_risk = self.inspect_png(path)
        except Exception:  # noqa: BLE001 - native renderer may emit a valid-but-unsupported PNG variant
            width, height, blank_risk = self.read_png_size(path)
        return ImageCheck(True, blank_risk, width, height, size)

    def inspect_png(self, path: Path) -> tuple[int, int, str]:
        chunks = self.read_png_chunks(path)
        ihdr = chunks.get("IHDR", [b""])[0]
        width, height, bit_depth, color_type = struct.unpack(">IIBB", ihdr[:10])
        if bit_depth != 8 or color_type not in {0, 2, 6}:
            return width, height, "unknown"
        channels = {0: 1, 2: 3, 6: 4}[color_type]
        raw = zlib.decompress(b"".join(chunks.get("IDAT", [])))
        stride = width * channels
        previous = [0] * stride
        offset = 0
        non_white = 0
        sampled = 0
        for _row in range(height):
            filter_type = raw[offset]
            offset += 1
            scanline = list(raw[offset : offset + stride])
            offset += stride
            recon = self.unfilter_scanline(scanline, previous, channels, filter_type)
            previous = recon
            for pixel in range(0, len(recon), channels):
                if channels == 4 and recon[pixel + 3] < 8:
                    sampled += 1
                    continue
                samples = recon[pixel : pixel + min(3, channels)]
                if len(samples) < 3 and samples:
                    samples = samples * 3
                if samples and not all(value >= 245 for value in samples[:3]):
                    non_white += 1
                sampled += 1
        if sampled == 0:
            return width, height, "high"
        ratio = non_white / sampled
        if width < 100 or height < 80 or ratio < 0.001:
            return width, height, "high"
        if ratio < 0.01:
            return width, height, "medium"
        return width, height, "low"

    @staticmethod
    def unfilter_scanline(scanline: list[int], previous: list[int], bpp: int, filter_type: int) -> list[int]:
        result = scanline[:]
        for index, value in enumerate(scanline):
            left = result[index - bpp] if index >= bpp else 0
            up = previous[index] if index < len(previous) else 0
            up_left = previous[index - bpp] if index >= bpp and index - bpp < len(previous) else 0
            if filter_type == 1:
                result[index] = (value + left) & 0xFF
            elif filter_type == 2:
                result[index] = (value + up) & 0xFF
            elif filter_type == 3:
                result[index] = (value + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                result[index] = (value + RenderValidateAgent.paeth(left, up, up_left)) & 0xFF
            elif filter_type != 0:
                raise ValueError(f"Unsupported PNG filter type: {filter_type}")
        return result

    @staticmethod
    def paeth(left: int, up: int, up_left: int) -> int:
        estimate = left + up - up_left
        distances = (abs(estimate - left), abs(estimate - up), abs(estimate - up_left))
        if distances[0] <= distances[1] and distances[0] <= distances[2]:
            return left
        if distances[1] <= distances[2]:
            return up
        return up_left

    def read_png_size(self, path: Path) -> tuple[int, int, str]:
        data = path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return 0, 0, "unknown"
        width, height = struct.unpack(">II", data[16:24])
        if width < 100 or height < 80:
            return width, height, "high"
        return width, height, "unknown"

    @staticmethod
    def read_png_chunks(path: Path) -> dict[str, list[bytes]]:
        data = path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Not a PNG file")
        offset = 8
        chunks: dict[str, list[bytes]] = {}
        while offset < len(data):
            length = struct.unpack(">I", data[offset : offset + 4])[0]
            name = data[offset + 4 : offset + 8].decode("ascii")
            chunk_data = data[offset + 8 : offset + 8 + length]
            chunks.setdefault(name, []).append(chunk_data)
            offset += 12 + length
            if name == "IEND":
                break
        return chunks

    def write_fallback_png(self, path: Path, diagram: dict[str, Any], source_text: str) -> None:
        try:
            self.write_readable_fallback_png(path, diagram, source_text)
            return
        except Exception as exc:  # noqa: BLE001 - keep fallback rendering non-blocking
            self.add_log(diagram["diagram_id"], "fallback_warning", f"Readable fallback PNG failed, using ASCII fallback: {exc}", [])
        self.write_ascii_fallback_png(path, diagram, source_text)

    def write_readable_fallback_png(self, path: Path, diagram: dict[str, Any], source_text: str) -> None:
        from PIL import Image, ImageDraw, ImageFont

        title = str(diagram.get("title") or diagram.get("diagram_id") or "系统架构图")
        layers = self.parse_layered_mermaid(source_text)
        if not layers:
            nodes = [{"title": label, "modules": []} for label in self.extract_flow_nodes(source_text)[:7]]
            layers = nodes or [{"title": title, "modules": []}]

        width = 1600
        header_height = 120
        layer_height = 150
        gap = 26
        margin_x = 70
        height = header_height + len(layers) * layer_height + max(0, len(layers) - 1) * gap + 70

        image = Image.new("RGB", (width, height), (255, 255, 255))
        draw = ImageDraw.Draw(image)
        font_title = self.load_font(34, bold=True)
        font_layer = self.load_font(25, bold=True)
        font_node = self.load_font(22)
        font_note = self.load_font(16)

        draw.rectangle((0, 0, width, 86), fill=(235, 241, 248))
        draw.rectangle((0, 86, width, 94), fill=(55, 82, 112))
        draw.text((margin_x, 28), title, font=font_title, fill=(25, 39, 52))
        draw.text((margin_x, 98), "fallback_rendered：未检测到 Mermaid CLI，已按 Mermaid 源码生成可读降级图。", font=font_note, fill=(120, 36, 36))

        layer_x = margin_x
        layer_w = width - margin_x * 2
        module_w = 390
        module_h = 52
        y = header_height
        colors = [
            ((230, 240, 251), (58, 93, 128)),
            ((234, 247, 240), (58, 114, 86)),
            ((250, 242, 225), (145, 101, 42)),
            ((241, 236, 250), (96, 80, 132)),
            ((236, 243, 244), (70, 103, 111)),
        ]
        centers: list[tuple[int, int]] = []
        for index, layer in enumerate(layers):
            fill, border = colors[index % len(colors)]
            draw.rounded_rectangle((layer_x, y, layer_x + layer_w, y + layer_height), radius=18, fill=fill, outline=border, width=3)
            draw.text((layer_x + 28, y + 24), str(layer["title"]), font=font_layer, fill=(24, 36, 48))
            modules = [str(item) for item in layer.get("modules", [])][:3]
            start_x = layer_x + 500
            if len(modules) == 1:
                positions = [start_x + 210]
            elif len(modules) == 2:
                positions = [start_x + 20, start_x + 450]
            else:
                positions = [start_x - 40, start_x + 360, start_x + 760]
            for module, mx in zip(modules, positions):
                my = y + 55
                draw.rounded_rectangle((mx, my, mx + module_w, my + module_h), radius=10, fill=(255, 255, 255), outline=border, width=2)
                self.draw_wrapped_text(draw, module, (mx + 18, my + 12), module_w - 36, font_node, (28, 47, 63))
            centers.append((layer_x + layer_w // 2, y + layer_height))
            if index > 0:
                previous = centers[index - 1]
                current_top = (layer_x + layer_w // 2, y)
                self.draw_arrow(draw, previous, current_top, (82, 98, 116))
            y += layer_height + gap

        image.save(path)

    def write_ascii_fallback_png(self, path: Path, diagram: dict[str, Any], source_text: str) -> None:
        width, height = 1200, 800
        canvas = bytearray([255, 255, 255] * width * height)
        self.fill_rect(canvas, width, 0, 0, width, 86, (238, 242, 246))
        self.fill_rect(canvas, width, 0, 86, width, 6, (70, 91, 118))
        self.draw_text(canvas, width, 32, 28, f"{diagram['diagram_id']} FALLBACK_RENDERED", (25, 39, 52), scale=3)
        self.draw_text(canvas, width, 32, 112, self.ascii_text(diagram["title"], "Diagram title unavailable"), (20, 20, 20), scale=2)
        self.draw_text(canvas, width, 32, 154, "Native Mermaid rendering was unavailable or failed.", (120, 36, 36), scale=2)

        nodes = self.extract_flow_nodes(source_text)
        y = 230
        if not nodes:
            nodes = ["Mermaid source preserved in .mmd", "Render status marked as fallback_rendered", "Review Gate must inspect this diagram"]
        for index, label in enumerate(nodes[:7], 1):
            x = 120 + (index % 2) * 420
            if index % 2 == 1 and index > 1:
                y += 120
            self.fill_rect(canvas, width, x, y, 340, 62, (230, 237, 244))
            self.stroke_rect(canvas, width, x, y, 340, 62, (68, 92, 115))
            self.draw_text(canvas, width, x + 18, y + 21, self.ascii_text(label, f"Node {index}")[:28], (31, 49, 68), scale=2)

        self.draw_text(canvas, width, 32, 724, "Install Mermaid CLI (mmdc) and rerun Render Validate for native PNG output.", (72, 82, 94), scale=2)
        self.write_png(path, width, height, bytes(canvas))

    def parse_layered_mermaid(self, source_text: str) -> list[dict[str, Any]]:
        node_labels = self.parse_mermaid_node_labels(source_text)
        layers: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for raw_line in source_text.splitlines():
            line = raw_line.strip()
            subgraph = re.match(r"subgraph\s+([A-Za-z][\w]*)\s*(?:\[\s*\"([^\"]+)\"\s*\]|\[([^\]]+)\])", line)
            if subgraph:
                title = (subgraph.group(2) or subgraph.group(3) or subgraph.group(1)).strip()
                current = {"title": title, "modules": []}
                layers.append(current)
                continue
            if line == "end":
                current = None
                continue
            if current is not None:
                node = re.match(r"([A-Za-z][\w]*)\s*(?:\[\s*\"([^\"]+)\"\s*\]|\[([^\]]+)\])", line)
                if node:
                    label = (node.group(2) or node.group(3) or node_labels.get(node.group(1)) or node.group(1)).strip()
                    if label and label != current["title"] and label not in current["modules"]:
                        current["modules"].append(label)
        if layers:
            return layers

        layer_ids = [node_id for node_id in node_labels if re.fullmatch(r"L\d+", node_id)]
        if layer_ids:
            return [{"title": node_labels[node_id], "modules": []} for node_id in layer_ids]
        return []

    @staticmethod
    def parse_mermaid_node_labels(source_text: str) -> dict[str, str]:
        labels: dict[str, str] = {}
        for raw_line in source_text.splitlines():
            line = raw_line.strip()
            match = re.match(r"([A-Za-z][\w]*)\s*(?:\[\s*\"([^\"]+)\"\s*\]|\[([^\]]+)\]|\(\s*\"?([^\")]+)\"?\s*\)|\{\s*\"?([^\"}]+)\"?\s*\})", line)
            if not match:
                continue
            label = next((group for group in match.groups()[1:] if group), "")
            label = label.strip()
            if label:
                labels[match.group(1)] = label
        return labels

    @staticmethod
    def load_font(size: int, bold: bool = False) -> Any:
        from PIL import ImageFont

        font_names = [
            "msyhbd.ttc" if bold else "msyh.ttc",
            "simhei.ttf",
            "simsun.ttc",
            "arial.ttf",
        ]
        font_dirs = [
            Path("C:/Windows/Fonts"),
            Path("/usr/share/fonts/truetype/dejavu"),
            Path("/usr/share/fonts/opentype/noto"),
        ]
        for directory in font_dirs:
            for name in font_names:
                candidate = directory / name
                if candidate.exists():
                    try:
                        return ImageFont.truetype(str(candidate), size)
                    except OSError:
                        continue
        return ImageFont.load_default()

    @staticmethod
    def draw_wrapped_text(draw: Any, text: str, xy: tuple[int, int], max_width: int, font: Any, fill: tuple[int, int, int]) -> None:
        x, y = xy
        lines: list[str] = []
        current = ""
        for char in text:
            trial = current + char
            box = draw.textbbox((0, 0), trial, font=font)
            if box[2] - box[0] <= max_width or not current:
                current = trial
            else:
                lines.append(current)
                current = char
        if current:
            lines.append(current)
        for line in lines[:2]:
            draw.text((x, y), line, font=font, fill=fill)
            y += 26

    @staticmethod
    def draw_arrow(draw: Any, start: tuple[int, int], end: tuple[int, int], fill: tuple[int, int, int]) -> None:
        sx, sy = start
        ex, ey = end
        draw.line((sx, sy + 4, ex, ey - 12), fill=fill, width=3)
        draw.polygon([(ex, ey - 4), (ex - 8, ey - 18), (ex + 8, ey - 18)], fill=fill)

    @staticmethod
    def extract_flow_nodes(source_text: str) -> list[str]:
        labels = []
        for match in re.finditer(r"[\w]+(?:\[[^\]]+\]|\([^)]+\)|\{[^}]+\})", source_text):
            token = match.group(0)
            label = re.sub(r"^[\w]+[\[\(\{]", "", token).rstrip("])}")
            label = label.strip().strip('"')
            if label and label not in labels:
                labels.append(label)
        return labels

    @staticmethod
    def ascii_text(value: Any, fallback: str) -> str:
        text = str(value or "")
        ascii_only = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in text)
        ascii_only = re.sub(r"\s+", " ", ascii_only).strip()
        return ascii_only or fallback

    @staticmethod
    def write_png(path: Path, width: int, height: int, rgb: bytes) -> None:
        rows = []
        stride = width * 3
        for y in range(height):
            rows.append(b"\x00" + rgb[y * stride : (y + 1) * stride])
        raw = b"".join(rows)

        def chunk(name: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + name + data + struct.pack(">I", binascii.crc32(name + data) & 0xFFFFFFFF)

        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(raw, level=6))
        png += chunk(b"IEND", b"")
        path.write_bytes(png)

    @staticmethod
    def fill_rect(canvas: bytearray, width: int, x: int, y: int, rect_width: int, rect_height: int, color: tuple[int, int, int]) -> None:
        for row in range(max(0, y), max(0, y + rect_height)):
            start = (row * width + x) * 3
            end = (row * width + x + rect_width) * 3
            canvas[start:end] = bytes(color) * rect_width

    @staticmethod
    def stroke_rect(canvas: bytearray, width: int, x: int, y: int, rect_width: int, rect_height: int, color: tuple[int, int, int]) -> None:
        RenderValidateAgent.fill_rect(canvas, width, x, y, rect_width, 2, color)
        RenderValidateAgent.fill_rect(canvas, width, x, y + rect_height - 2, rect_width, 2, color)
        RenderValidateAgent.fill_rect(canvas, width, x, y, 2, rect_height, color)
        RenderValidateAgent.fill_rect(canvas, width, x + rect_width - 2, y, 2, rect_height, color)

    def draw_text(self, canvas: bytearray, width: int, x: int, y: int, text: str, color: tuple[int, int, int], scale: int = 2) -> None:
        cursor = x
        for char in text.upper():
            if char == " ":
                cursor += 4 * scale
                continue
            bitmap = FONT_5X7.get(char, FONT_5X7.get("?"))
            if bitmap:
                for row, bits in enumerate(bitmap):
                    for col in range(5):
                        if bits & (1 << (4 - col)):
                            self.fill_rect(canvas, width, cursor + col * scale, y + row * scale, scale, scale, color)
            cursor += 6 * scale

    def load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"缺少 Render Validate Agent 必需输入：{path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - convert to stage failure
            raise RuntimeError(f"无法解析 JSON 输入：{path}") from exc

    def add_log(self, diagram_id: str, stage: str, message: str, errors: list[dict[str, Any]]) -> None:
        self.log_events.append({"diagram_id": diagram_id, "stage": stage, "message": message, "errors": errors})

    def path_from_workspace(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else self.workspace / path

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace.resolve())).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    @staticmethod
    def require_fields(obj: dict[str, Any], fields: list[str], label: str) -> None:
        missing = [field for field in fields if field not in obj]
        if missing:
            raise RuntimeError(f"{label} 缺少字段：{', '.join(missing)}")

    @staticmethod
    def unique(values: Any) -> list[str]:
        return list(dict.fromkeys(str(value) for value in values if str(value).strip()))

    @staticmethod
    def shorten(value: str, max_length: int) -> str:
        value = re.sub(r"\s+", " ", str(value)).strip()
        if len(value) <= max_length:
            return value
        return value[: max_length - 3] + "..."

    @staticmethod
    def escape_md(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")


FONT_5X7 = {
    "A": [0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
    "B": [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
    "C": [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
    "D": [0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E],
    "E": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
    "F": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
    "G": [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0E],
    "H": [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
    "I": [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "J": [0x07, 0x02, 0x02, 0x02, 0x12, 0x12, 0x0C],
    "K": [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
    "L": [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
    "M": [0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11],
    "N": [0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11],
    "O": [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "P": [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
    "Q": [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
    "R": [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
    "S": [0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E],
    "T": [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
    "U": [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
    "V": [0x11, 0x11, 0x11, 0x11, 0x0A, 0x0A, 0x04],
    "W": [0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A],
    "X": [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
    "Y": [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
    "Z": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
    "0": [0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E],
    "1": [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
    "2": [0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F],
    "3": [0x1E, 0x01, 0x01, 0x0E, 0x01, 0x01, 0x1E],
    "4": [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
    "5": [0x1F, 0x10, 0x10, 0x1E, 0x01, 0x01, 0x1E],
    "6": [0x0E, 0x10, 0x10, 0x1E, 0x11, 0x11, 0x0E],
    "7": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
    "8": [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
    "9": [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x01, 0x0E],
    "-": [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
    "_": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F],
    ".": [0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C],
    ":": [0x00, 0x0C, 0x0C, 0x00, 0x0C, 0x0C, 0x00],
    "/": [0x01, 0x02, 0x02, 0x04, 0x08, 0x08, 0x10],
    "(": [0x02, 0x04, 0x08, 0x08, 0x08, 0x04, 0x02],
    ")": [0x08, 0x04, 0x02, 0x02, 0x02, 0x04, 0x08],
    "?": [0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04],
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Render Validate Agent.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root.")
    parser.add_argument("--records-dir", type=Path, default=Path("output/records"), help="Directory containing diagram-specs.json.")
    parser.add_argument("--output-dir", type=Path, default=Path("output/records"), help="Published record output directory.")
    parser.add_argument("--renderer-command", default=None, help="Mermaid CLI-compatible command. Defaults to mmdc on PATH.")
    parser.add_argument("--timeout-seconds", type=int, default=60, help="Native renderer timeout per diagram.")
    parser.add_argument("--disable-fallback", action="store_true", help="Fail diagrams instead of creating fallback PNGs.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    workspace = args.workspace.resolve()
    records_dir = args.records_dir if args.records_dir.is_absolute() else workspace / args.records_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else workspace / args.output_dir

    agent = RenderValidateAgent(
        workspace=workspace,
        records_dir=records_dir,
        output_dir=output_dir,
        renderer_command=args.renderer_command,
        timeout_seconds=args.timeout_seconds,
        disable_fallback=args.disable_fallback,
    )
    paths = agent.run()
    print(f"Render Validate Agent completed: {agent.run_id}")
    print(f"staging: {paths['staging_dir']}")
    print(f"published: {paths['published_dir']}")
    print(f"output: {paths['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
