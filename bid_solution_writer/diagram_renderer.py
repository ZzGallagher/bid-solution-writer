from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from .models import DiagramSpec


def render_diagrams(diagrams: list[DiagramSpec], renderer_command: str | None = None) -> list[DiagramSpec]:
    renderer = resolve_renderer(renderer_command)
    rendered: list[DiagramSpec] = []
    for diagram in diagrams:
        mmd = Path(diagram.mermaid_path)
        png = mmd.with_suffix(".png")
        if renderer:
            native = try_native_render(renderer, mmd, png)
            if native is None:
                rendered.append(replace(diagram, image_path=str(png), render_status="native_rendered", error=None))
                continue
        try:
            write_internal_png(png, diagram)
            rendered.append(replace(diagram, image_path=str(png), render_status="internal_rendered", error=None))
        except Exception as exc:  # noqa: BLE001
            rendered.append(replace(diagram, image_path=None, render_status="failed", error=f"内置图表渲染失败：{exc}"))
    return rendered


def resolve_renderer(renderer_command: str | None) -> str | None:
    if renderer_command:
        candidate = Path(renderer_command)
        if candidate.exists():
            return str(candidate)
        import shutil

        return shutil.which(renderer_command)
    return None


def try_native_render(renderer: str, mmd: Path, png: Path) -> str | None:
    try:
        completed = subprocess.run(
            [renderer, "-i", str(mmd), "-o", str(png), "-b", "transparent"],
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    if completed.returncode == 0 and png.exists() and png.stat().st_size > 0:
        return None
    return (completed.stderr or completed.stdout or f"mmdc exited {completed.returncode}").strip()


def write_internal_png(path: Path, diagram: DiagramSpec) -> None:
    from PIL import Image, ImageDraw, ImageFont

    layers = parse_layers(diagram.mermaid)
    if not layers:
        layers = [{"title": diagram.title, "modules": extract_labels(diagram.mermaid)[:6]}]
    width = 1500
    layer_h = 132
    gap = 28
    top = 70
    height = top + len(layers) * layer_h + max(0, len(layers) - 1) * gap + 70
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font_title = load_font(30)
    font_label = load_font(22)
    font_small = load_font(18)
    draw.text((60, 24), diagram.title, font=font_title, fill=(20, 20, 20))
    y = top
    centers = []
    for idx, layer in enumerate(layers):
        x = 70
        w = width - 140
        draw.rounded_rectangle((x, y, x + w, y + layer_h), radius=4, fill=(238, 238, 238), outline=(150, 150, 150), width=2)
        draw.text((x + 18, y + 18), layer["title"], font=font_title if idx == 0 and len(layers) == 1 else font_label, fill=(20, 20, 20))
        modules = layer.get("modules", [])[:5]
        if modules:
            module_w = min(230, (w - 70) // max(1, len(modules)) - 16)
            start_x = x + 40
            module_y = y + 64
            for module_idx, label in enumerate(modules):
                mx = start_x + module_idx * (module_w + 24)
                draw.rectangle((mx, module_y, mx + module_w, module_y + 42), fill=(255, 255, 255), outline=(150, 150, 150), width=2)
                draw_centered_text(draw, label, (mx, module_y, mx + module_w, module_y + 42), font_small)
        centers.append((width // 2, y + layer_h))
        if idx:
            px, py = centers[idx - 1]
            draw.line((px, py + 2, width // 2, y - 8), fill=(0, 0, 0), width=2)
            draw.polygon([(width // 2, y), (width // 2 - 6, y - 10), (width // 2 + 6, y - 10)], fill=(0, 0, 0))
        y += layer_h + gap
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def parse_layers(source: str) -> list[dict[str, list[str] | str]]:
    layers = []
    current = None
    for raw in source.splitlines():
        line = raw.strip()
        if line.startswith("subgraph "):
            title = extract_label_from_line(line) or line.split(" ", 1)[1]
            current = {"title": title, "modules": []}
            layers.append(current)
        elif line == "end":
            current = None
        elif current is not None:
            label = extract_label_from_line(line)
            if label and label != current["title"]:
                current["modules"].append(label)
    return layers


def extract_labels(source: str) -> list[str]:
    labels = []
    for line in source.splitlines():
        label = extract_label_from_line(line.strip())
        if label and label not in labels:
            labels.append(label)
    return labels


def extract_label_from_line(line: str) -> str | None:
    for left, right in (('["', '"]'), ("[(", ")]"), ('("', '")'), ('{"', '"}')):
        if left in line and right in line:
            return line.split(left, 1)[1].split(right, 1)[0]
    if "[" in line and "]" in line:
        return line.split("[", 1)[1].split("]", 1)[0].strip('"')
    if "(" in line and ")" in line:
        return line.split("(", 1)[1].split(")", 1)[0].strip('"')
    if "{" in line and "}" in line:
        return line.split("{", 1)[1].split("}", 1)[0].strip('"')
    return None


def load_font(size: int):
    from PIL import ImageFont

    for candidate in (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf"), Path("C:/Windows/Fonts/simsun.ttc")):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    return ImageFont.load_default()


def draw_centered_text(draw, text: str, box, font) -> None:
    left, top, right, bottom = box
    text = text if len(text) <= 14 else text[:13] + "…"
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text((left + (right - left - width) / 2, top + (bottom - top - height) / 2 - 1), text, font=font, fill=(20, 20, 20))
