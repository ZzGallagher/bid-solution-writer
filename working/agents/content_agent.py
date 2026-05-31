#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Content Agent.

This stage consumes the requirement fact source and the design blueprint, then
emits Word-assembly-ready content blocks. Prose generation is intentionally
routed through ``llm_client.call_llm_api``. Fill the actual LLM API call only in
``working/agents/llm_client.py``.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from llm_client import LLMAPIUnavailable, call_llm_api


AGENT_NAME = "Content Agent"
AGENT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"

REQ_ID_RE = re.compile(r"^(T|P|Q|B|D)[0-9]{3}$")
SCORE_ID_RE = re.compile(r"^S[0-9]{3}$")

HIGH_RISK_KEYWORDS = {
    "personnel": ("浜哄憳", "璐熻矗浜?", "鍥㈤槦", "绀句繚", "椹诲満", "璁插笀", "宸ョ▼甯?"),
    "qualification": ("璧勮川", "璇佷功", "鑱岀О", "璁よ瘉"),
    "performance_case": ("涓氱哗", "妗堜緥", "鍚堝悓", "楠屾敹鎶ュ憡", "閿€鍞?"),
    "price": ("鎶ヤ环", "閲戦", "璐圭敤", "浠樻", "淇濊瘉閲?", "浠锋牸"),
    "delivery_date": ("浜や粯", "宸ユ湡", "涓婄嚎", "杩涘満", "鍛ㄦ湡", "鏃ユ湡"),
    "service_commitment": ("璐ㄤ繚", "淇濅慨", "鍝嶅簲鏃堕棿", "鍝嶅簲鏃堕檺", "涓婇棬", "7脳24", "7*24", "24灏忔椂", "鎵胯"),
}

OVERCOMMITMENT_REPLACEMENTS = {
    "瀹屽叏婊¤冻": "鍝嶅簲",
    "鏃犲亸绂?": "鎸夎姹傚搷搴?",
    "闆跺亸绂?": "鎸夎姹傚搷搴?",
    "纭繚": "鏀拺",
    "淇濊瘉": "淇濋殰",
    "鎵胯": "鍝嶅簲",
    "鍥哄畾鍝嶅簲鏃堕棿": "鍝嶅簲鏃堕檺",
    "鏈€鐭椂闂?": "鍚堢悊鏃堕棿",
    "姘镐箙": "闀挎湡",
}

UNRESOLVED_PLACEHOLDER_PATTERNS = ("銆怗EN:", "銆怌OPY:", "銆怰EVIEW:", "{{", "}}", "TODO", "TBD")



class ContentAgent:
    def __init__(
        self,
        workspace: Path,
        records_dir: Path,
        output_dir: Path,
        model: str | None = None,
        allow_local_draft: bool = False,
    ) -> None:
        self.workspace = workspace
        self.records_dir = records_dir
        self.output_dir = output_dir
        self.model = model
        self.allow_local_draft = allow_local_draft
        now = datetime.now().astimezone()
        self.generated_at = now.isoformat(timespec="seconds")
        self.run_id = f"RUN-{now:%Y%m%d-%H%M%S}"
        self.review_index = 1
        self.confirm_index = 1
        self.review_items: list[dict[str, Any]] = []
        self.confirm_items: list[dict[str, Any]] = []
        self.notes: list[str] = []

    def run(self) -> dict[str, Path]:
        requirements = self.load_json(self.records_dir / "requirements.json")
        matrix = self.load_json(self.records_dir / "requirements-matrix.json")
        design = self.load_json(self.records_dir / "design-blueprint.json")
        section_plan = self.load_text(self.records_dir / "section-plan.md")

        self.run_id = str(requirements.get("run_id") or design.get("run_id") or self.run_id)
        req_index = self.requirement_index(requirements)
        score_index = self.scoring_index(requirements)
        module_index = {item["module_id"]: item for item in design.get("modules", []) if item.get("module_id")}
        diagram_plan = design.get("diagram_plan", [])

        blocks: list[dict[str, Any]] = []
        for index, section in enumerate(design.get("sections", []), 1):
            block = self.build_block(index, section, req_index, score_index, module_index, diagram_plan, section_plan)
            blocks.append(block)

        self.import_requirement_lifecycle_items(requirements, blocks)

        artifact = {
            "schema_version": SCHEMA_VERSION,
            "artifact": "content-blocks",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": self.producer(),
            "inputs": self.build_inputs(requirements, matrix, design),
            "blocks": blocks,
            "review_items": self.review_items,
            "confirm_items": self.confirm_items,
        }
        self.validate_content_blocks(artifact, req_index, score_index)

        staging_dir = self.workspace / "working" / "agent-system" / "staging" / "content" / self.run_id
        published_dir = self.workspace / "working" / "agent-system" / "published" / "content" / self.run_id
        for directory in (staging_dir, published_dir, self.output_dir):
            directory.mkdir(parents=True, exist_ok=True)

        outputs = {
            "content-blocks.json": json.dumps(artifact, ensure_ascii=False, indent=2) + "\n",
            "content-preview.md": self.render_preview(artifact),
            "content-review-notes.md": self.render_review_notes(artifact),
        }
        for name, content in outputs.items():
            (staging_dir / name).write_text(content, encoding="utf-8")
        for name in outputs:
            shutil.copy2(staging_dir / name, published_dir / name)
            shutil.copy2(staging_dir / name, self.output_dir / name)

        return {"staging_dir": staging_dir, "published_dir": published_dir, "output_dir": self.output_dir}

    def build_block(
        self,
        index: int,
        section: dict[str, Any],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        module_index: dict[str, dict[str, Any]],
        diagram_plan: list[dict[str, Any]],
        section_plan: str,
    ) -> dict[str, Any]:
        block_id = f"CB{index:03d}"
        req_ids = self.unique_valid_req_ids(section.get("source_requirement_ids", []))
        score_ids = self.unique_score_ids(section.get("related_scoring_item_ids", []))
        status = self.status_for_section(section, req_ids, score_ids, req_index, score_index)
        content_type = section.get("content_type", "generated_paragraphs")

        if content_type == "confirm_placeholder":
            block_type = "confirm_placeholder"
            content: Any = {"placeholder_text": section.get("placeholder", "銆怌ONFIRM:寰呬汉宸ョ‘璁ゃ€?")}
        elif content_type == "diagram_reference":
            diagram_id = self.diagram_for_section(section, diagram_plan)
            block_type = "diagram_reference"
            content = {"diagram_id": diagram_id, "caption": self.diagram_caption(diagram_id, diagram_plan, req_ids)}
        elif content_type == "generated_table":
            block_type = "table"
            content = self.generate_table_content(section, req_ids, score_ids, req_index, score_index)
        else:
            if content_type == "dynamic_sections":
                block_type = "rich_content"
            else:
                block_type = "review_text" if content_type == "review_text" else "paragraphs"
            content = self.generate_paragraphs(section, req_ids, score_ids, req_index, score_index, module_index, section_plan, diagram_plan)

        risk_flags = self.risk_flags_for_content(content)
        if risk_flags and status == "generated":
            status = "review_required"
            self.notes.append(f"{block_id} 鍖呭惈楂橀闄╁叧閿瘝锛屽凡杞叆 review_required銆?")

        block: dict[str, Any] = {
            "block_id": block_id,
            "placeholder": section["placeholder"],
            "section_id": section["section_id"],
            "type": block_type,
            "content": content,
            "source_requirement_ids": req_ids,
            "scoring_item_ids": score_ids,
            "status": status,
        }
        diagram_ids = self.diagram_ids_in_content(content)
        if block_type == "diagram_reference":
            diagram_ids = [content["diagram_id"]]
        if diagram_ids:
            block["diagram_ids"] = diagram_ids
        if risk_flags:
            block["risk_flags"] = risk_flags
        review_notes = self.review_notes_for_block(section, status, risk_flags)
        if review_notes:
            block["review_notes"] = review_notes

        if status == "confirm_required":
            self.add_confirm_item(block_id, f"{section['title']} 闇€浜哄伐纭鍚庡啀瑁呴厤銆?", req_ids + score_ids)
        elif status == "review_required":
            self.add_review_item(block_id, f"{section['title']} 闇€澶嶆牳鏉ユ簮銆佽〃杈捐竟鐣屽拰璇勫垎椤瑰搷搴斻€?", req_ids + score_ids)

        return block

    def generate_paragraphs(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        module_index: dict[str, dict[str, Any]],
        section_plan: str,
        diagram_plan: list[dict[str, Any]] | None = None,
    ) -> list[Any]:
        payload = self.build_llm_payload(section, req_ids, score_ids, req_index, score_index, module_index, section_plan)
        try:
            response = call_llm_api(payload)
            paragraphs = self.normalize_llm_response(response)
        except LLMAPIUnavailable:
            if not self.allow_local_draft:
                raise
            paragraphs = self.local_draft(section, req_ids, score_ids, req_index, score_index, module_index, diagram_plan or [])
        return [self.sanitize_content_item(item) for item in paragraphs if self.content_item_text(item).strip()]

    def build_llm_payload(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        module_index: dict[str, dict[str, Any]],
        section_plan: str,
    ) -> dict[str, Any]:
        modules = [module_index[module_id] for module_id in section.get("module_ids", []) if module_id in module_index]
        return {
            "task": "generate_content_block",
            "agent": AGENT_NAME,
            "schema_version": SCHEMA_VERSION,
            "model": self.model,
            "section": {
                "section_id": section["section_id"],
                "title": section["title"],
                "content_type": section["content_type"],
                "status": section["status"],
                "placeholder": section["placeholder"],
            },
            "requirements": [self.source_brief(req_index[req_id]) for req_id in req_ids if req_id in req_index],
            "scoring_items": [self.score_brief(score_index[score_id]) for score_id in score_ids if score_id in score_index],
            "modules": [
                {
                    "module_id": module["module_id"],
                    "name": module["name"],
                    "responsibility": module["responsibility"],
                    "source_requirement_ids": module.get("source_requirement_ids", []),
                    "related_scoring_item_ids": module.get("related_scoring_item_ids", []),
                }
                for module in modules
            ],
            "section_plan_excerpt": section_plan[:4000],
            "rules": [
                "杈撳嚭蹇呴』鏄彲鐩存帴绮樿创杩涙姇鏍囨妧鏈柟妗堢殑姝ｆ枃锛屼笉鍐欐枃妗ｇ敓鎴愪换鍔¤鏄庯紝涓嶈В閲婁綘濡備綍瑕嗙洊闇€姹傘€佽瘎鍒嗛」鎴栬拷婧疘D銆?",
                "鍙緷鎹?requirements銆乻coring_items銆乵odules 鎻愮偧涓氬姟鑳屾櫙銆佺郴缁熻兘鍔涖€佹妧鏈矾寰勫拰瀹炴柦浠峰€笺€?",
                "姣忔鍦ㄨ涔変笂蹇呴』鑳借拷婧埌 payload 涓殑 ID锛屼絾姝ｆ枃涓笉瑕佸嚭鐜版潵婧怚D銆侀渶姹傜煩闃点€佽璁¤摑鍥俱€乸ayload銆佸崰浣嶇绛夊唴閮ㄨ繃绋嬭瘝銆?",
                "缂栧啓鐩殑銆侀」鐩儗鏅€佸缓璁惧唴瀹圭瓑鎬昏堪绔犺妭瑕佸啓鎴愬畬鏁磋嚜鐒舵锛屾寜鑳屾櫙鎸戞垬銆佺郴缁熷缓璁惧唴瀹广€佷笟鍔′环鍊笺€佹湰鏂囨。浣滅敤灞曞紑銆?",
                "鏋舵瀯璁捐鍜屽姛鑳借璁＄珷鑺傝浠庤緭鍏ャ€佸鐞嗐€佽緭鍑恒€佹帶鍒躲€佸紓甯稿拰楠岃瘉瑙掑害鎻忚堪绯荤粺鏈韩锛屼笉瑕佸啓鎴愮珷鑺傚畨鎺掓垨璧勬枡娓呭崟銆?",
                "涓嶆柊澧炰汉鍛樸€佽祫璐ㄣ€佷笟缁┿€佹姤浠枫€佸懆鏈熴€佹湇鍔℃壙璇虹瓑浜嬪疄銆?",
                "娑夊強 REVIEW 鐨勫唴瀹瑰啓鎴愬垵绋夸絾淇濇寔鍙鏍歌〃杈撅紱娑夊強 CONFIRM 鐨勪簨瀹炰笉瑕佽ˉ榻愩€?",
                "閬垮厤浣跨敤鈥滀繚璇併€佸畬鍏ㄦ弧瓒炽€佹棤鍋忕銆佺‘淇濄€佹壙璇恒€佹案涔呪€濈瓑鏃犳潵婧愬己鎵胯鎺緸銆?",
                "杈撳嚭 3 鍒?6 涓寮忋€佺ǔ鍋ャ€侀€傚悎鎶曟爣鎶€鏈柟妗堢殑涓枃娈佃惤銆?",
            ],
            "output_contract": {"content": ["娈佃惤1", "娈佃惤2"]},
        }

    def local_draft(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        module_index: dict[str, dict[str, Any]],
        diagram_plan: list[dict[str, Any]] | None = None,
    ) -> list[Any]:
        title = section["title"]
        context = self.local_context(section, req_ids, score_ids, req_index, score_index, module_index, diagram_plan or [])

        if section.get("content_type") == "dynamic_sections":
            return self.local_dynamic_function_sections(context)
        if "鎬讳綋鏋舵瀯" in title:
            return self.local_architecture_overview(context)
        if "鏋舵瀯鍥捐鏄?" in title:
            return self.local_architecture_diagram_text(context)
        if "璁捐鍘熷垯" in title:
            return self.local_design_principles(context)
        if "閮ㄧ讲" in title:
            return self.local_deployment_design(context)
        if "鍔熻兘璁捐鎬昏堪" in title:
            return self.local_function_overview(context)
        if "鏁版嵁搴?" in title or "鏍稿績涓氬姟鏁版嵁" in title:
            return self.local_data_design(context)
        if "鎬ц兘" in title:
            return self.local_performance_design(context)
        if "鍙潬" in title:
            return self.local_reliability_design(context)
        if "缁翠慨" in title or "淇濋殰鎬?" in title:
            return self.local_maintainability_design(context)
        if "娴嬭瘯" in title:
            return self.local_testability_design(context)
        if "瀹夊叏" in title:
            return self.local_security_design(context)
        if "鐜" in title:
            return self.local_environment_design(context)
        if "鍏抽敭鎶€鏈?" in title:
            return self.local_key_technology_design(context)
        if "璐ㄩ噺" in title or "椋庨櫓" in title:
            return self.local_quality_risk_design(context)
        if "浜や粯" in title or "楠屾敹" in title or "椤圭洰杩涘害" in title:
            return self.local_delivery_design(context)
        if "鍩硅" in title or "鍞悗" in title or "搴旀€?" in title or "璺熻釜" in title:
            return self.local_service_design(context)
        if "缂栧啓鐩殑" in title or "椤圭洰鑳屾櫙" in title:
            return self.local_project_purpose(context)
        if "寤鸿鍐呭" in title:
            return self.local_construction_scope(context)
        return self.local_generic_design(context)

    def local_context(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
        module_index: dict[str, dict[str, Any]],
        diagram_plan: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        requirements = [req_index[req_id] for req_id in req_ids if req_id in req_index]
        scoring_items = [score_index[score_id] for score_id in score_ids if score_id in score_index]
        modules = [module_index[module_id] for module_id in section.get("module_ids", []) if module_id in module_index]
        all_text = " ".join(
            [section.get("title", ""), section.get("placeholder", "")]
            + [item.get("title", "") + " " + item.get("text", "") for item in requirements]
            + [item.get("name", "") + " " + item.get("responsibility", "") for item in modules]
        )
        source_label = ", ".join(req_ids[:8] + score_ids[:4])
        if len(req_ids) + len(score_ids) > 12:
            source_label += "..."
        return {
            "section": section,
            "title": section["title"],
            "requirements": requirements,
            "scoring_items": scoring_items,
            "modules": modules,
            "req_ids": req_ids,
            "score_ids": score_ids,
            "req_titles": [item.get("title", item.get("requirement_id", "")) for item in requirements],
            "score_titles": [item.get("title", item.get("scoring_item_id", "")) for item in scoring_items],
            "module_names": [item.get("name", "") for item in modules],
            "source_label": source_label or "璁捐钃濆浘",
            "profile": self.detect_profile(all_text),
            "all_text": all_text,
            "diagram_by_req": self.diagram_by_requirement(diagram_plan or []),
        }

    @staticmethod
    def detect_profile(text: str) -> str:
        lower = text.lower()
        if "drone" in lower or "uav" in lower:
            return "drone_inspection"
        if "chart" in lower or "ais" in lower or "radar" in lower:
            return "electronic_chart"
        return "generic"

    @staticmethod
    def project_label(context: dict[str, Any]) -> str:
        return str(context.get("project_name") or "the project")

    def local_project_purpose(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_construction_scope(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_architecture_overview(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_architecture_diagram_text(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_design_principles(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_deployment_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_function_overview(self, context: dict[str, Any]) -> list[str]:
        title = context.get("title") or "Function design"
        sources = self.join_titles(context.get("req_titles", []))
        return [
            f"{title} is drafted from the structured requirement source. It summarizes the target capability, processing flow, outputs, and quality controls without adding unsupported commitments.",
            f"The implementation should keep traceability to {sources} and preserve review or confirmation items for manual checking.",
        ]

    def local_dynamic_function_sections(self, context: dict[str, Any]) -> list[str]:
        items: list[str] = []
        for requirement in context.get("requirements", [])[:12]:
            items.extend(self.local_requirement_function_paragraphs(requirement, context))
        if items:
            return items
        for module in context.get("modules", [])[:12]:
            name = str(module.get("name") or "Module")
            req_ids = [req_id for req_id in module.get("source_requirement_ids", []) if req_id in context.get("req_ids", [])][:6]
            items.extend(self.local_module_paragraphs(name, ", ".join(req_ids) or "design blueprint"))
        return items or self.local_function_overview(context)

    def local_requirement_function_paragraphs(self, requirement: dict[str, Any], context: dict[str, Any]) -> list[str]:
        req_id = str(requirement.get("requirement_id", ""))
        title = str(requirement.get("title") or requirement.get("text") or "Requirement")
        text = str(requirement.get("text") or title)
        diagram_id = self.diagram_by_requirement(context.get("diagram_plan", [])).get(req_id)
        diagram_text = f" Related diagram: {diagram_id}." if diagram_id else ""
        return [
            f"For {title}, the solution should organize input handling, processing logic, result output, exception handling, and verification around the source requirement {req_id}.{diagram_text}",
            f"The draft basis is: {self.shorten(text, 180)}",
        ]

    @staticmethod
    def trim_sentence_end(value: str) -> str:
        return str(value).strip().rstrip(".;:,")

    @staticmethod
    def function_subject(title: str, text: str) -> str:
        return str(title or text or "the function")

    @staticmethod
    def function_scene(title: str, text: str, profile: str) -> str:
        return "the applicable business scenario"

    def function_mechanism(self, title: str, text: str, profile: str) -> str:
        return "structured input, processing, output, and verification controls"

    def function_output(self, title: str, text: str, profile: str) -> str:
        return "traceable processing results and reviewable records"

    def function_validation(self, title: str, text: str, profile: str) -> str:
        return "configuration review, functional test, integration test, and acceptance check"

    @staticmethod
    def diagram_by_requirement(diagram_plan: list[dict[str, Any]]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for diagram in diagram_plan or []:
            diagram_id = str(diagram.get("diagram_id") or "")
            for req_id in diagram.get("source_requirement_ids", []) or []:
                mapping.setdefault(str(req_id), diagram_id)
        return mapping

    def local_module_paragraphs(self, name: str, source_text: str) -> list[str]:
        return [
            f"{name} is planned as a traceable module linked to {source_text}. It should expose clear inputs, outputs, responsibilities, and verification records.",
            f"{name} should avoid unsupported commitments and keep any uncertain facts in the review or confirmation workflow.",
        ]

    def local_data_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_performance_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_reliability_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_maintainability_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_testability_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_security_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_environment_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_key_technology_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_quality_risk_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_delivery_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_service_design(self, context: dict[str, Any]) -> list[str]:
        return self.local_generic_design(context)

    def local_generic_design(self, context: dict[str, Any]) -> list[str]:
        title = context.get("title") or "Proposal section"
        sources = self.join_titles(context.get("req_titles", []))
        return [
            f"{title} is generated from the structured requirement source and should remain traceable to {sources}.",
            "This local draft is intended for offline pipeline verification only; production prose should come from llm_client.call_llm_api().",
        ]
    def generate_table_content(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        rows: list[list[str]] = []
        for req_id in req_ids:
            item = req_index.get(req_id)
            if not item:
                continue
            rows.append([req_id, item.get("title", req_id), self.shorten(item.get("text", item.get("name", "")), 120)])
        for score_id in score_ids:
            item = score_index.get(score_id)
            if not item:
                continue
            rows.append([score_id, f"璇勫垎椤癸細{item.get('title', score_id)}", self.shorten(item.get("text", ""), 120)])
        return {"columns": ["鏉ユ簮ID", "鍝嶅簲瀵硅薄", "姝ｆ枃鍝嶅簲瑕佺偣"], "rows": rows or [["-", section["title"], "寰呰ˉ鍏呮潵婧愬悗鐢熸垚銆?"]]}

    def diagram_for_section(self, section: dict[str, Any], diagram_plan: list[dict[str, Any]]) -> str:
        section_id = section["section_id"]
        req_ids = set(section.get("source_requirement_ids", []))
        for diagram in diagram_plan:
            if section_id in diagram.get("related_section_ids", []):
                return diagram["diagram_id"]
        for diagram in diagram_plan:
            if req_ids & set(diagram.get("source_requirement_ids", [])):
                return diagram["diagram_id"]
        raise RuntimeError(f"{section_id} 鏃犳硶鍖归厤璁捐钃濆浘涓殑 diagram_id銆?")

    def diagram_caption(self, diagram_id: str, diagram_plan: list[dict[str, Any]], req_ids: list[str]) -> str:
        diagram = next((item for item in diagram_plan if item.get("diagram_id") == diagram_id), {})
        title = diagram.get("title", diagram_id)
        return f"{title}锛岀敤浜庤鏄庢湰鑺傜浉鍏虫ā鍧椼€佹暟鎹祦鎴栨祦绋嬪叧绯汇€?"

    def import_requirement_lifecycle_items(self, requirements: dict[str, Any], blocks: list[dict[str, Any]]) -> None:
        for item in requirements.get("confirm_candidates", []):
            status = item.get("status")
            source_ids = self.unique(item.get("source_ids", []))
            block_id = self.find_block_for_sources(blocks, source_ids)
            if status == "confirm_required":
                self.add_confirm_item(block_id, item.get("reason") or item.get("field") or "闇€浜哄伐纭銆?", source_ids)
            elif status == "review_required":
                self.add_review_item(block_id, item.get("reason") or item.get("field") or "闇€澶嶆牳銆?", source_ids)

    def find_block_for_sources(self, blocks: list[dict[str, Any]], source_ids: list[str]) -> str:
        if source_ids:
            source_set = set(source_ids)
            for block in blocks:
                block_sources = set(block.get("source_requirement_ids", [])) | set(block.get("scoring_item_ids", []))
                if source_set & block_sources:
                    return block["block_id"]
        confirm_block = next((block for block in blocks if block.get("status") == "confirm_required"), None)
        if confirm_block:
            return confirm_block["block_id"]
        review_block = next((block for block in blocks if block.get("status") == "review_required"), None)
        return review_block["block_id"] if review_block else blocks[0]["block_id"]

    def add_review_item(self, block_id: str, message: str, source_ids: list[str]) -> None:
        key = (block_id, tuple(source_ids), message)
        for item in self.review_items:
            if (item["block_id"], tuple(item.get("source_ids", [])), item["message"]) == key:
                return
        self.review_items.append(
            {
                "item_id": f"RV{self.review_index:03d}",
                "block_id": block_id,
                "message": message,
                "source_ids": self.unique_source_ids(source_ids),
                "status": "review_required",
            }
        )
        self.review_index += 1

    def add_confirm_item(self, block_id: str, message: str, source_ids: list[str]) -> None:
        key = (block_id, tuple(source_ids), message)
        for item in self.confirm_items:
            if (item["block_id"], tuple(item.get("source_ids", [])), item["message"]) == key:
                return
        self.confirm_items.append(
            {
                "item_id": f"CF{self.confirm_index:03d}",
                "block_id": block_id,
                "message": message,
                "source_ids": self.unique_source_ids(source_ids),
                "status": "confirm_required",
            }
        )
        self.confirm_index += 1

    def validate_content_blocks(
        self,
        artifact: dict[str, Any],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
    ) -> None:
        self.require_fields(
            artifact,
            ["schema_version", "artifact", "run_id", "generated_at", "producer", "inputs", "blocks", "review_items", "confirm_items"],
            "content-blocks.json",
        )
        if artifact["artifact"] != "content-blocks" or artifact["schema_version"] != SCHEMA_VERSION:
            raise RuntimeError("content-blocks.json artifact 鎴?schema_version 涓嶇鍚堝绾︺€?")
        block_ids = [block.get("block_id") for block in artifact["blocks"]]
        if len(block_ids) != len(set(block_ids)):
            raise RuntimeError("content-blocks.json block_id 瀛樺湪閲嶅銆?")
        known_req_ids = set(req_index)
        known_score_ids = set(score_index)
        for block in artifact["blocks"]:
            self.require_fields(
                block,
                ["block_id", "placeholder", "section_id", "type", "content", "source_requirement_ids", "scoring_item_ids", "status"],
                f"content-blocks.json#{block.get('block_id', '?')}",
            )
            if not re.match(r"^CB[0-9]{3}$", block["block_id"]):
                raise RuntimeError(f"Invalid block_id: {block['block_id']}")
            if not block["source_requirement_ids"] and not block["scoring_item_ids"] and block["status"] != "confirm_required":
                raise RuntimeError(f"{block['block_id']} missing source IDs")
            invalid_req_ids = set(block["source_requirement_ids"]) - known_req_ids
            invalid_score_ids = set(block["scoring_item_ids"]) - known_score_ids
            if invalid_req_ids:
                raise RuntimeError(f"{block['block_id']} references unknown requirement IDs: {', '.join(sorted(invalid_req_ids))}")
            if invalid_score_ids:
                raise RuntimeError(f"{block['block_id']} references unknown scoring IDs: {', '.join(sorted(invalid_score_ids))}")
            text = self.content_text(block["content"])
            unresolved = [pattern for pattern in UNRESOLVED_PLACEHOLDER_PATTERNS if pattern in text]
            if unresolved:
                raise RuntimeError(f"{block['block_id']} has unresolved placeholders: {', '.join(unresolved)}")

        valid_block_ids = set(block_ids)
        for field in ("review_items", "confirm_items"):
            prefix = "RV" if field == "review_items" else "CF"
            item_ids = [item.get("item_id") for item in artifact[field]]
            if len(item_ids) != len(set(item_ids)):
                raise RuntimeError(f"{field} item_id duplicated")
            for item in artifact[field]:
                self.require_fields(item, ["item_id", "block_id", "message", "source_ids", "status"], field)
                if not re.match(rf"^{prefix}[0-9]{{3}}$", item["item_id"]):
                    raise RuntimeError(f"Invalid {field} item_id: {item['item_id']}")
                if item["block_id"] not in valid_block_ids:
                    raise RuntimeError(f"{field} references unknown block_id: {item['block_id']}")

    def load_json(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"Missing Content Agent input: {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - convert to stage failure
            raise RuntimeError(f"Unable to parse JSON input: {path}") from exc

    @staticmethod
    def load_text(path: Path) -> str:
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def build_inputs(self, requirements: dict[str, Any], matrix: dict[str, Any], design: dict[str, Any]) -> list[dict[str, str]]:
        inputs = [
            {"artifact": "requirements", "path": "output/records/requirements.json", "schema_version": str(requirements.get("schema_version", "unknown"))},
            {"artifact": "requirements-matrix", "path": "output/records/requirements-matrix.json", "schema_version": str(matrix.get("schema_version", "unknown"))},
            {"artifact": "design-blueprint", "path": "output/records/design-blueprint.json", "schema_version": str(design.get("schema_version", "unknown"))},
        ]
        if (self.records_dir / "section-plan.md").exists():
            inputs.append({"artifact": "section-plan", "path": "output/records/section-plan.md", "schema_version": "markdown"})
        rules = self.workspace / "docs" / "contracts" / "validation-rules.md"
        if rules.exists():
            inputs.append({"artifact": "generation-rules", "path": self.relative(rules), "schema_version": "markdown"})
        return inputs

    def producer(self) -> dict[str, str]:
        producer = {"agent": AGENT_NAME, "version": AGENT_VERSION}
        if self.model:
            producer["model"] = self.model
        elif self.allow_local_draft:
            producer["model"] = "local-draft"
        return producer

    @staticmethod
    def requirement_index(requirements: dict[str, Any]) -> dict[str, dict[str, Any]]:
        index = {item["requirement_id"]: item for item in requirements.get("requirements", []) if item.get("requirement_id")}
        for item in requirements.get("delivery_items", []):
            delivery_id = item.get("delivery_id")
            if not delivery_id:
                continue
            index[delivery_id] = {
                "requirement_id": delivery_id,
                "category": "delivery",
                "title": item.get("name", delivery_id),
                "text": ", ".join(str(part) for part in (item.get("name"), item.get("quantity"), item.get("medium")) if part),
                "source": item.get("source", {}),
                "status": item.get("status", "extracted"),
                "risk_level": "normal",
            }
        return index

    @staticmethod
    def scoring_index(requirements: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {item["scoring_item_id"]: item for item in requirements.get("scoring_items", []) if item.get("scoring_item_id")}

    def status_for_section(
        self,
        section: dict[str, Any],
        req_ids: list[str],
        score_ids: list[str],
        req_index: dict[str, dict[str, Any]],
        score_index: dict[str, dict[str, Any]],
    ) -> str:
        if section.get("content_type") == "confirm_placeholder" or section.get("status") == "confirm_required":
            return "confirm_required"
        statuses = [req_index.get(req_id, {}).get("status") for req_id in req_ids]
        statuses.extend(score_index.get(score_id, {}).get("status") for score_id in score_ids)
        if section.get("content_type") == "review_text" or section.get("status") == "review_required" or "review_required" in statuses:
            return "review_required"
        return "generated"

    @staticmethod
    def source_brief(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("requirement_id"),
            "category": item.get("category"),
            "title": item.get("title"),
            "text": item.get("text"),
            "status": item.get("status"),
            "risk_level": item.get("risk_level"),
            "source_quote": item.get("source", {}).get("quote"),
        }

    @staticmethod
    def score_brief(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": item.get("scoring_item_id"),
            "title": item.get("title"),
            "text": item.get("text"),
            "score": item.get("score"),
            "response_section": item.get("response_section"),
            "status": item.get("status"),
        }

    @staticmethod
    def normalize_llm_response(response: dict[str, Any] | list[str] | str) -> list[str]:
        if isinstance(response, dict):
            content = response.get("content")
            if isinstance(content, list):
                return [str(item).strip() for item in content if str(item).strip()]
            if isinstance(content, str):
                return [item.strip() for item in re.split(r"\n\s*\n|\n", content) if item.strip()]
        if isinstance(response, list):
            return [str(item).strip() for item in response if str(item).strip()]
        if isinstance(response, str):
            return [item.strip() for item in re.split(r"\n\s*\n|\n", response) if item.strip()]
        raise RuntimeError("LLM API 杩斿洖鏍煎紡涓嶇鍚?Content Agent 濂戠害銆?")

    @staticmethod
    def sanitize_claims(value: str) -> str:
        text = re.sub(r"\s+", " ", value).strip()
        for source, replacement in OVERCOMMITMENT_REPLACEMENTS.items():
            text = text.replace(source, replacement)
        return text

    def sanitize_content_item(self, item: Any) -> Any:
        if isinstance(item, str):
            return self.sanitize_claims(item)
        if isinstance(item, dict):
            sanitized = dict(item)
            if "text" in sanitized:
                sanitized["text"] = self.sanitize_claims(str(sanitized["text"]))
            if "caption" in sanitized:
                sanitized["caption"] = self.sanitize_claims(str(sanitized["caption"]))
            return sanitized
        return item

    @staticmethod
    def content_item_text(item: Any) -> str:
        if isinstance(item, dict):
            return " ".join(str(value) for value in item.values())
        return str(item or "")

    @staticmethod
    def diagram_ids_in_content(content: Any) -> list[str]:
        ids: list[str] = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "diagram" and item.get("diagram_id"):
                    ids.append(str(item["diagram_id"]))
        elif isinstance(content, dict) and content.get("diagram_id"):
            ids.append(str(content["diagram_id"]))
        return list(dict.fromkeys(ids))

    @staticmethod
    def risk_flags_for_content(content: Any) -> list[str]:
        text = ContentAgent.content_text(content)
        flags = [flag for flag, keywords in HIGH_RISK_KEYWORDS.items() if any(keyword in text for keyword in keywords)]
        return list(dict.fromkeys(flags))

    @staticmethod
    def review_notes_for_block(section: dict[str, Any], status: str, risk_flags: list[str]) -> list[str]:
        notes = []
        if status == "review_required":
            notes.append("Review Gate should verify sources, boundaries, and scoring responses.")
        if status == "confirm_required":
            notes.append("CONFIRM placeholder remains for manual confirmation before Word assembly.")
        if risk_flags:
            notes.append("Detected high-risk facts: " + ", ".join(risk_flags))
        if section.get("content_type") == "review_text":
            notes.append("This block is review_text and should be checked manually.")
        return notes

    def render_preview(self, artifact: dict[str, Any]) -> str:
        lines = [
            "# Content Preview",
            "",
            f"- Run ID: `{artifact['run_id']}`",
            f"- Generated At: `{artifact['generated_at']}`",
            "",
        ]
        for block in artifact["blocks"]:
            lines.extend([f"## {block['block_id']} {block['placeholder']}", "", f"- Status: `{block['status']}`", f"- Source IDs: {', '.join(block['source_requirement_ids'] + block['scoring_item_ids']) or '-'}", ""])
            content = block["content"]
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "diagram":
                        lines.append(f"![{item.get('caption', item.get('diagram_id', 'diagram'))}]({item.get('diagram_id')})")
                    elif isinstance(item, dict):
                        lines.append(str(item.get("text") or item.get("caption") or item))
                    else:
                        lines.append(str(item))
            elif isinstance(content, dict) and block["type"] == "table":
                lines.append("| " + " | ".join(content["columns"]) + " |")
                lines.append("|" + "|".join("---" for _ in content["columns"]) + "|")
                for row in content["rows"]:
                    lines.append("| " + " | ".join(self.escape_md(cell) for cell in row) + " |")
            else:
                lines.append(self.content_text(content))
            lines.append("")
        return "\n".join(lines)

    def render_review_notes(self, artifact: dict[str, Any]) -> str:
        lines = [
            "# Content Review Notes",
            "",
            f"- Run ID: `{artifact['run_id']}`",
            f"- Generated At: `{artifact['generated_at']}`",
            "",
            "## Review Items",
            "",
        ]
        lines.extend(self.render_lifecycle_table(artifact["review_items"]))
        lines.extend(["", "## Confirm Items", ""])
        lines.extend(self.render_lifecycle_table(artifact["confirm_items"]))
        if self.notes:
            lines.extend(["", "## Agent Notes", ""])
            lines.extend(f"- {note}" for note in self.notes)
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def render_lifecycle_table(items: list[dict[str, Any]]) -> list[str]:
        if not items:
            return ["鏈鏈櫥璁扮浉鍏充簨椤广€?"]
        lines = ["| ID | Block | Status | Source IDs | Message |", "|---|---|---|---|---|"]
        for item in items:
            lines.append(
                f"| {item['item_id']} | {item['block_id']} | {item['status']} | {', '.join(item.get('source_ids', [])) or '-'} | {ContentAgent.escape_md(item['message'])} |"
            )
        return lines

    @staticmethod
    def content_text(content: Any) -> str:
        if isinstance(content, list):
            return " ".join(str(item) for item in content)
        if isinstance(content, dict):
            parts: list[str] = []
            for value in content.values():
                if isinstance(value, list):
                    parts.extend(str(item) for item in value)
                elif isinstance(value, dict):
                    parts.extend(str(item) for item in value.values())
                else:
                    parts.append(str(value))
            return " ".join(parts)
        return str(content or "")

    @staticmethod
    def require_fields(obj: dict[str, Any], fields: list[str], label: str) -> None:
        missing = [field for field in fields if field not in obj]
        if missing:
            raise RuntimeError(f"{label} missing fields: {', '.join(missing)}")

    @staticmethod
    def unique(values: Any) -> list[str]:
        return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))

    def unique_valid_req_ids(self, values: Any) -> list[str]:
        return [value for value in self.unique(values) if REQ_ID_RE.match(value)]

    def unique_score_ids(self, values: Any) -> list[str]:
        return [value for value in self.unique(values) if SCORE_ID_RE.match(value)]

    def unique_source_ids(self, values: Any) -> list[str]:
        return [value for value in self.unique(values) if REQ_ID_RE.match(value) or SCORE_ID_RE.match(value)]

    @staticmethod
    def join_titles(values: list[str]) -> str:
        values = [value for value in dict.fromkeys(values) if value]
        if not values:
            return "pending"
        text = ", ".join(values)
        return text if len(text) <= 80 else text[:79] + "..."

    @staticmethod
    def shorten(value: str, max_length: int) -> str:
        value = re.sub(r"\s+", " ", str(value)).strip()
        if len(value) <= max_length:
            return value
        return value[: max_length - 1] + "..."

    @staticmethod
    def escape_md(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace.resolve())).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Content Agent.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root.")
    parser.add_argument("--records-dir", type=Path, default=Path("output/records"), help="Directory containing published upstream artifacts.")
    parser.add_argument("--output-dir", type=Path, default=Path("output/records"), help="Published record output directory.")
    parser.add_argument("--model", default=None, help="Model label recorded in producer metadata.")
    parser.add_argument(
        "--allow-local-draft",
        action="store_true",
        help="Use deterministic local draft text only when call_llm_api is not filled. Intended for schema verification.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    workspace = args.workspace.resolve()
    records_dir = args.records_dir if args.records_dir.is_absolute() else workspace / args.records_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else workspace / args.output_dir

    agent = ContentAgent(
        workspace=workspace,
        records_dir=records_dir,
        output_dir=output_dir,
        model=args.model,
        allow_local_draft=args.allow_local_draft,
    )
    paths = agent.run()
    print(f"Content Agent completed: {agent.run_id}")
    print(f"staging: {paths['staging_dir']}")
    print(f"published: {paths['published_dir']}")
    print(f"output: {paths['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
