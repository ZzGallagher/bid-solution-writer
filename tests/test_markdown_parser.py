from pathlib import Path

from bid_solution_writer.markdown_parser import parse_markdown
from bid_solution_writer.pipeline import build_function_blocks, build_requirement_blocks
from bid_solution_writer.content_generator import ContentGenerator
from bid_solution_writer.prompt_templates import architecture_payload, background_payload, function_design_payload, performance_payload


def test_parse_uav_requirements_shape():
    parsed = parse_markdown(Path("input/高压线路无人机巡检方案技术要求.md"))
    assert parsed.project_overview
    assert parsed.function_requirements.body
    assert parsed.performance_requirements.body
    assert parsed.non_functional_requirements.body
    assert len(parsed.function_groups) == 5
    assert sum(len(group.points) for group in parsed.function_groups) == 11


def test_section_mapping_contains_confirmed_chapters(tmp_path):
    parsed = parse_markdown(Path("input/高压线路无人机巡检方案技术要求.md"))
    requirement_blocks = build_requirement_blocks(parsed)
    function_blocks, _ = build_function_blocks(ContentGenerator(allow_local_draft=True), parsed, tmp_path)
    ids = {block.section_id for block in requirement_blocks + function_blocks}
    assert {"2.1", "2.2", "2.3", "3.2"}.issubset(ids)
    assert "3.2.1.1" in ids
    assert "3.2.5.2" in ids


def test_dynamic_prompts_include_confirmed_writing_workflow():
    parsed = parse_markdown(Path("input/高压线路无人机巡检方案技术要求.md"))
    first_point = parsed.function_groups[0].points[0]
    payloads = [
        background_payload(parsed),
        architecture_payload(parsed),
        function_design_payload(parsed.function_groups[0].title, first_point, "3.2.1.1"),
        performance_payload(parsed.performance_items),
    ]
    for payload in payloads:
        workflow = payload["writing_workflow"]
        assert workflow["name"] == "confirmed_solution_writing_workflow"
        assert any("说明和实际任务调用外接 API 生成" in step for step in workflow["steps"])
        assert any("保存可修改的 .mmd 源码" in step for step in workflow["steps"])
