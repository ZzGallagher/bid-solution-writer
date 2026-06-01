#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Review Gate Agent.

This stage is the quality gate before Word assembly. It consumes the published
pipeline artifacts, checks coverage and risk rules, then emits a release
decision plus human-readable review lists.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


AGENT_NAME = "Review Gate Agent"
AGENT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"

EXPECTED_JSON_ARTIFACTS = {
    "requirements": "requirements.json",
    "requirements-matrix": "requirements-matrix.json",
    "design-blueprint": "design-blueprint.json",
    "content-blocks": "content-blocks.json",
    "diagram-manifest": "diagram-manifest.json",
}

HIGH_RISK_KEYWORDS = {
    "personnel": ("人员", "负责人", "团队", "社保", "驻场", "讲师", "工程师"),
    "qualification": ("资质", "证书", "职称", "认证"),
    "performance_case": ("业绩", "案例", "合同", "验收报告", "销售"),
    "price": ("报价", "金额", "费用", "付款", "保证金", "价格"),
    "delivery_date": ("交付", "工期", "上线", "进场", "周期", "日期"),
    "service_commitment": ("质保", "保修", "响应时间", "响应时限", "上门", "7×24", "7*24", "24小时", "承诺"),
}

OVERCOMMITMENT_PATTERNS = (
    "保证",
    "完全满足",
    "无偏离",
    "零偏离",
    "确保",
    "承诺",
    "固定响应时间",
    "最短时间",
    "永久",
)

UNRESOLVED_PLACEHOLDER_PATTERNS = (
    "【GEN:",
    "【COPY:",
    "【REVIEW:",
    "{{",
    "}}",
    "TODO",
    "TBD",
)

TARGET_PLACEHOLDERS = {
    "【GEN:总体架构设计】",
    "【GEN:架构图说明】",
    "【GEN:功能设计总述】",
    "【GEN:功能设计章节】",
}

PROCESS_LEAK_PATTERNS = (
    "来源需求",
    "需求事实源",
    "结构化需求事实源",
    "payload",
    "占位符",
    "本章节按方案撰写要求",
    "本地草稿",
    "local-draft",
    "Content Agent",
    "Design Agent",
    "Review Gate",
)

MIN_PRIMARY_FUNCTION_FLOW_DIAGRAMS = 5

REVIEW_STATUSES = {"review_required"}
CONFIRM_STATUSES = {"confirm_required"}
CLOSED_ITEM_STATUSES = {"resolved", "waived"}


@dataclass
class ArtifactLoad:
    key: str
    filename: str
    path: Path
    data: dict[str, Any] | None
    error: str | None = None


class ReviewGateAgent:
    def __init__(self, workspace: Path, records_dir: Path, output_dir: Path) -> None:
        self.workspace = workspace
        self.records_dir = records_dir
        self.output_dir = output_dir
        now = datetime.now().astimezone()
        self.generated_at = now.isoformat(timespec="seconds")
        self.run_id = f"RUN-{now:%Y%m%d-%H%M%S}"
        self.issues: list[dict[str, Any]] = []
        self.gates: list[dict[str, Any]] = []
        self.issue_index = 1
        self.gate_index = 1

    def run(self) -> dict[str, Any]:
        artifacts = self.load_artifacts()
        requirements = self.artifact_data(artifacts, "requirements")
        if requirements and requirements.get("run_id"):
            self.run_id = str(requirements["run_id"])

        self.check_required_artifacts(artifacts)
        self.check_schema_basics(artifacts)

        requirements = self.artifact_data(artifacts, "requirements") or {}
        matrix = self.artifact_data(artifacts, "requirements-matrix") or {}
        design = self.artifact_data(artifacts, "design-blueprint") or {}
        content = self.artifact_data(artifacts, "content-blocks") or {}
        diagrams = self.artifact_data(artifacts, "diagram-manifest") or {}

        coverage_summary = self.check_coverage(requirements, matrix, content, diagrams)
        self.check_source_ids(requirements, design, content, diagrams)
        self.check_confirm_and_review_items(requirements, content)
        self.check_risk_claims(requirements, content)
        self.check_target_content_quality(content)
        self.check_diagrams(design, content, diagrams)
        self.check_placeholder_log()

        decision = "blocked" if any(issue["blocking"] for issue in self.issues) else "approved"
        release_decision = {
            "schema_version": SCHEMA_VERSION,
            "artifact": "release-decision",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": {"agent": AGENT_NAME, "version": AGENT_VERSION},
            "inputs": self.build_inputs(artifacts),
            "decision": decision,
            "allow_word_assembly": decision == "approved",
            "quality_gates": self.gates,
            "issues": self.issues,
            "confirm_items": self.build_checklist(content, "confirm_items", "CF"),
            "review_items": self.build_checklist(content, "review_items", "RV"),
            "coverage_summary": coverage_summary,
            "next_actions": self.next_actions(),
        }
        self.validate_release_decision(release_decision)

        staging_dir = self.workspace / "working" / "agent-system" / "staging" / "review" / self.run_id
        published_dir = self.workspace / "working" / "agent-system" / "published" / "review" / self.run_id
        for directory in (staging_dir, published_dir, self.output_dir):
            directory.mkdir(parents=True, exist_ok=True)

        outputs = {
            "release-decision.json": json.dumps(release_decision, ensure_ascii=False, indent=2) + "\n",
            "review-report.md": self.render_review_report(release_decision),
            "coverage-check.md": self.render_coverage_check(release_decision),
            "人工确认清单.md": self.render_checklist("人工确认清单", release_decision["confirm_items"]),
            "复核清单.md": self.render_checklist("复核清单", release_decision["review_items"]),
        }

        for name, content_text in outputs.items():
            (staging_dir / name).write_text(content_text, encoding="utf-8")
        for name in outputs:
            shutil.copy2(staging_dir / name, published_dir / name)
            shutil.copy2(staging_dir / name, self.output_dir / name)

        return {
            "staging_dir": staging_dir,
            "published_dir": published_dir,
            "output_dir": self.output_dir,
            "decision": decision,
        }

    def load_artifacts(self) -> dict[str, ArtifactLoad]:
        loaded: dict[str, ArtifactLoad] = {}
        for key, filename in EXPECTED_JSON_ARTIFACTS.items():
            path = self.records_dir / filename
            if not path.exists():
                loaded[key] = ArtifactLoad(key, filename, path, None, "missing")
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - convert parser errors to gate issues
                loaded[key] = ArtifactLoad(key, filename, path, None, f"invalid_json: {exc}")
                continue
            loaded[key] = ArtifactLoad(key, filename, path, data)
        return loaded

    @staticmethod
    def artifact_data(artifacts: dict[str, ArtifactLoad], key: str) -> dict[str, Any] | None:
        return artifacts.get(key, ArtifactLoad(key, "", Path(), None)).data

    def check_required_artifacts(self, artifacts: dict[str, ArtifactLoad]) -> None:
        for key, artifact in artifacts.items():
            if artifact.error == "missing":
                self.add_issue(
                    "blocking",
                    "schema_invalid",
                    f"缺少 Review Gate 必需输入产物：{artifact.filename}。",
                    artifact.filename,
                    artifact.filename,
                    self.owner_for_artifact(key),
                    "先完成对应上游 Agent 并发布该产物，再重新运行 Review Gate。",
                )
            elif artifact.error:
                self.add_issue(
                    "blocking",
                    "schema_invalid",
                    f"{artifact.filename} 无法解析为有效 JSON：{artifact.error}。",
                    artifact.filename,
                    artifact.filename,
                    self.owner_for_artifact(key),
                    "修复 JSON 格式或重新生成上游产物。",
                )
        self.add_gate(
            "必需产物检查",
            "failed" if any(item.error for item in artifacts.values()) else "passed",
            "所有必需输入产物均可读取。" if not any(item.error for item in artifacts.values()) else "存在缺失或不可解析的必需产物。",
        )

    def check_schema_basics(self, artifacts: dict[str, ArtifactLoad]) -> None:
        for key, artifact in artifacts.items():
            if not artifact.data:
                continue
            expected_artifact = key
            actual_artifact = artifact.data.get("artifact")
            if artifact.data.get("schema_version") != SCHEMA_VERSION or actual_artifact != expected_artifact:
                self.add_issue(
                    "blocking",
                    "schema_invalid",
                    f"{artifact.filename} 的 schema_version 或 artifact 字段不符合契约。",
                    artifact.filename,
                    actual_artifact or artifact.filename,
                    self.owner_for_artifact(key),
                    "按 docs/contracts/schemas 中的契约重新生成该产物。",
                )

    def check_coverage(
        self,
        requirements: dict[str, Any],
        matrix: dict[str, Any],
        content: dict[str, Any],
        diagrams: dict[str, Any],
    ) -> dict[str, int]:
        requirement_ids = [item["requirement_id"] for item in requirements.get("requirements", []) if item.get("requirement_id")]
        scoring_ids = [item["scoring_item_id"] for item in requirements.get("scoring_items", []) if item.get("scoring_item_id")]
        writing_ids = [item["writing_requirement_id"] for item in requirements.get("writing_requirements", []) if item.get("writing_requirement_id")]
        content_req_ids = self.ids_from_blocks(content, "source_requirement_ids")
        content_score_ids = self.ids_from_blocks(content, "scoring_item_ids")
        content_writing_ids = self.ids_from_blocks(content, "writing_requirement_ids")
        checklist_source_ids = self.ids_from_lifecycle(content)
        diagram_req_ids = {
            req_id
            for diagram in diagrams.get("diagrams", [])
            if diagram.get("assembly_allowed") is True and diagram.get("render_status") not in {"failed", "skipped"}
            for req_id in diagram.get("source_requirement_ids", [])
        }

        covered_requirements = content_req_ids | diagram_req_ids | checklist_source_ids
        uncovered_requirements = [req_id for req_id in requirement_ids if req_id not in covered_requirements]
        for req_id in uncovered_requirements:
            requirement = self.find_by_id(requirements.get("requirements", []), "requirement_id", req_id)
            severity = "blocking" if requirement.get("mandatory") or requirement.get("risk_level") in {"high", "critical"} else "major"
            self.add_issue(
                severity,
                "uncovered_requirement",
                f"需求 {req_id} 未映射到正文、可装配图表、人工确认项或复核项。",
                "requirements.json",
                req_id,
                "Design Agent",
                "补充设计覆盖、正文内容块或复核/确认清单记录。",
            )

        covered_scoring = content_score_ids | {item for item in checklist_source_ids if item.startswith("S")}
        uncovered_scoring = [score_id for score_id in scoring_ids if score_id not in covered_scoring]
        for score_id in uncovered_scoring:
            self.add_issue(
                "blocking",
                "unanswered_scoring_item",
                f"评分项 {score_id} 未在正文内容块或复核清单中明确响应。",
                "requirements.json",
                score_id,
                "Content Agent",
                "补充绑定该评分项的正文段落、表格或复核项。",
            )

        covered_writing = content_writing_ids | {item for item in checklist_source_ids if item.startswith("WR")}
        uncovered_writing = [writing_id for writing_id in writing_ids if writing_id not in covered_writing]
        for writing_id in uncovered_writing:
            self.add_issue(
                "blocking",
                "uncovered_writing_requirement",
                f"方案撰写要求 {writing_id} 未扩写进入正文内容块或复核清单。",
                "requirements.json",
                writing_id,
                "Content Agent",
                "补充绑定该撰写要求的正文内容块；低置信度映射需同时进入复核清单。",
            )

        review_items = content.get("review_items", [])
        for writing_item in requirements.get("writing_requirements", []):
            writing_id = writing_item.get("writing_requirement_id")
            if not writing_id:
                continue
            if writing_item.get("mapping_confidence") == "low" and not self.lifecycle_has_any_source(review_items, {writing_id}):
                self.add_issue(
                    "blocking",
                    "review_missing",
                    f"低置信度自动映射的方案撰写要求 {writing_id} 未进入复核清单。",
                    "requirements.json",
                    writing_id,
                    "Content Agent",
                    "将该撰写要求登记到 content-blocks.review_items。",
                )

        matrix_uncovered = [
            row.get("source_id")
            for row in matrix.get("rows", [])
            if row.get("coverage_status") == "uncovered" and row.get("source_id")
        ]
        for source_id in matrix_uncovered:
            self.add_issue(
                "blocking",
                "unanswered_scoring_item" if source_id.startswith("S") else "uncovered_writing_requirement" if source_id.startswith("WR") else "uncovered_requirement",
                f"需求矩阵已标记 {source_id} 为 uncovered。",
                "requirements-matrix.json",
                source_id,
                "Design Agent",
                "先在需求矩阵和设计蓝图中补齐覆盖路径。",
            )

        status = "failed" if uncovered_requirements or uncovered_scoring or uncovered_writing or matrix_uncovered else "passed"
        self.add_gate(
            "覆盖率与评分项响应",
            status,
            f"需求覆盖 {len(requirement_ids) - len(uncovered_requirements)}/{len(requirement_ids)}，评分项响应 {len(scoring_ids) - len(uncovered_scoring)}/{len(scoring_ids)}，方案撰写要求扩写 {len(writing_ids) - len(uncovered_writing)}/{len(writing_ids)}。",
        )

        diagram_total = len(diagrams.get("diagrams", []))
        diagram_allowed = sum(1 for diagram in diagrams.get("diagrams", []) if diagram.get("assembly_allowed") is True)
        return {
            "requirements_total": len(requirement_ids),
            "requirements_covered": len(requirement_ids) - len(uncovered_requirements),
            "requirements_uncovered": len(uncovered_requirements),
            "writing_requirements_total": len(writing_ids),
            "writing_requirements_covered": len(writing_ids) - len(uncovered_writing),
            "writing_requirements_uncovered": len(uncovered_writing),
            "scoring_items_total": len(scoring_ids),
            "scoring_items_covered": len(scoring_ids) - len(uncovered_scoring),
            "diagram_total": diagram_total,
            "diagram_assembly_allowed": diagram_allowed,
        }

    def check_source_ids(
        self,
        requirements: dict[str, Any],
        design: dict[str, Any],
        content: dict[str, Any],
        diagrams: dict[str, Any],
    ) -> None:
        valid_req_ids = {item.get("requirement_id") for item in requirements.get("requirements", [])}
        valid_req_ids.update(item.get("delivery_id") for item in requirements.get("delivery_items", []))
        valid_score_ids = {item.get("scoring_item_id") for item in requirements.get("scoring_items", [])}
        valid_writing_ids = {item.get("writing_requirement_id") for item in requirements.get("writing_requirements", [])}
        missing_count = 0

        for block in content.get("blocks", []):
            req_ids = set(block.get("source_requirement_ids", []))
            score_ids = set(block.get("scoring_item_ids", []))
            writing_ids = set(block.get("writing_requirement_ids", []))
            if not req_ids and not score_ids and not writing_ids and block.get("status") not in CONFIRM_STATUSES:
                missing_count += 1
                self.add_issue(
                    "blocking",
                    "missing_source_id",
                    f"内容块 {block.get('block_id')} 缺少需求 ID、评分项 ID 或方案撰写要求 ID。",
                    "content-blocks.json",
                    block.get("block_id", "unknown"),
                    "Content Agent",
                    "为内容块补充 source_requirement_ids、scoring_item_ids 或 writing_requirement_ids。",
                )
            for req_id in req_ids - valid_req_ids:
                missing_count += 1
                self.add_issue("blocking", "missing_source_id", f"内容块引用了不存在的需求 ID：{req_id}。", "content-blocks.json", block.get("block_id", "unknown"), "Content Agent", "修正为 requirements.json 中存在的需求 ID。")
            for score_id in score_ids - valid_score_ids:
                missing_count += 1
                self.add_issue("blocking", "missing_source_id", f"内容块引用了不存在的评分项 ID：{score_id}。", "content-blocks.json", block.get("block_id", "unknown"), "Content Agent", "修正为 requirements.json 中存在的评分项 ID。")
            for writing_id in writing_ids - valid_writing_ids:
                missing_count += 1
                self.add_issue("blocking", "missing_source_id", f"内容块引用了不存在的方案撰写要求 ID：{writing_id}。", "content-blocks.json", block.get("block_id", "unknown"), "Content Agent", "修正为 requirements.json 中存在的 writing_requirement_id。")

        for section in design.get("sections", []):
            if not section.get("source_requirement_ids") and not section.get("related_scoring_item_ids") and not section.get("writing_requirement_ids"):
                missing_count += 1
                self.add_issue("major", "missing_source_id", f"设计章节 {section.get('section_id')} 缺少来源 ID。", "design-blueprint.json", section.get("section_id", "unknown"), "Design Agent", "为章节补充 source_requirement_ids 或 related_scoring_item_ids。")
            for writing_id in set(section.get("writing_requirement_ids", [])) - valid_writing_ids:
                missing_count += 1
                self.add_issue("blocking", "missing_source_id", f"设计章节引用了不存在的方案撰写要求 ID：{writing_id}。", "design-blueprint.json", section.get("section_id", "unknown"), "Design Agent", "修正为 requirements.json 中存在的 writing_requirement_id。")

        for diagram in diagrams.get("diagrams", []):
            req_ids = set(diagram.get("source_requirement_ids", []))
            if not req_ids:
                missing_count += 1
                self.add_issue("blocking", "missing_source_id", f"图表 {diagram.get('diagram_id')} 缺少 source_requirement_ids。", "diagram-manifest.json", diagram.get("diagram_id", "unknown"), "Render Validate Agent", "回到 Mermaid Agent 补充图表来源需求 ID。")
            for req_id in req_ids - valid_req_ids:
                missing_count += 1
                self.add_issue("blocking", "missing_source_id", f"图表引用了不存在的需求 ID：{req_id}。", "diagram-manifest.json", diagram.get("diagram_id", "unknown"), "Render Validate Agent", "修正为 requirements.json 中存在的需求 ID。")

        self.add_gate("来源 ID 绑定", "failed" if missing_count else "passed", "所有正文与图表均有有效来源 ID。" if not missing_count else f"发现 {missing_count} 个来源 ID 问题。")

    def check_confirm_and_review_items(self, requirements: dict[str, Any], content: dict[str, Any]) -> None:
        confirm_items = content.get("confirm_items", [])
        review_items = content.get("review_items", [])
        issue_count = 0

        for block in content.get("blocks", []):
            block_id = block.get("block_id", "unknown")
            block_text = self.content_text(block)
            if block.get("status") in CONFIRM_STATUSES:
                has_item = any(item.get("block_id") == block_id for item in confirm_items)
                preserves_confirm = block.get("type") == "confirm_placeholder" or "CONFIRM" in block_text
                if not has_item or not preserves_confirm:
                    issue_count += 1
                    self.add_issue(
                        "blocking",
                        "confirm_replaced",
                        f"内容块 {block_id} 标记为 confirm_required，但未同时保留 CONFIRM 占位符并进入人工确认清单。",
                        "content-blocks.json",
                        block_id,
                        "Content Agent",
                        "恢复 CONFIRM 占位符，并在 confirm_items 中登记。",
                    )
            if block.get("status") in REVIEW_STATUSES and not any(item.get("block_id") == block_id for item in review_items):
                issue_count += 1
                self.add_issue(
                    "blocking",
                    "review_missing",
                    f"内容块 {block_id} 标记为 review_required，但未进入复核清单。",
                    "content-blocks.json",
                    block_id,
                    "Content Agent",
                    "在 review_items 中登记该内容块的复核事项。",
                )
            unresolved = [pattern for pattern in UNRESOLVED_PLACEHOLDER_PATTERNS if pattern in block_text]
            if unresolved:
                issue_count += 1
                self.add_issue(
                    "blocking",
                    "placeholder_unresolved",
                    f"内容块 {block_id} 存在未处理占位符：{', '.join(unresolved)}。",
                    "content-blocks.json",
                    block_id,
                    "Content Agent",
                    "替换 GEN/COPY 占位符；REVIEW 内容应生成初稿并进入复核清单。",
                )

        required_confirm_sources = [
            item for item in requirements.get("confirm_candidates", []) if item.get("status") in CONFIRM_STATUSES
        ]
        for item in required_confirm_sources:
            source_ids = set(item.get("source_ids", []))
            if source_ids and not self.lifecycle_has_any_source(confirm_items, source_ids):
                issue_count += 1
                self.add_issue(
                    "blocking",
                    "confirm_replaced",
                    f"需求抽取阶段的确认项 {item.get('item_id')} 未进入人工确认清单。",
                    "requirements.json",
                    item.get("item_id", "unknown"),
                    "Content Agent",
                    "将该确认项传递到 content-blocks.confirm_items，并保留 CONFIRM。",
                )

        required_review_sources = [
            item for item in requirements.get("confirm_candidates", []) if item.get("status") in REVIEW_STATUSES
        ]
        for item in required_review_sources:
            source_ids = set(item.get("source_ids", []))
            if source_ids and not self.lifecycle_has_any_source(review_items, source_ids):
                issue_count += 1
                self.add_issue(
                    "blocking",
                    "review_missing",
                    f"需求抽取阶段的复核项 {item.get('item_id')} 未进入复核清单。",
                    "requirements.json",
                    item.get("item_id", "unknown"),
                    "Content Agent",
                    "将该事项传递到 content-blocks.review_items。",
                )

        self.add_gate("CONFIRM 与 REVIEW 留痕", "failed" if issue_count else "passed", "确认与复核事项已进入对应清单。" if not issue_count else f"发现 {issue_count} 个确认/复核留痕问题。")

    def check_risk_claims(self, requirements: dict[str, Any], content: dict[str, Any]) -> None:
        issue_count = 0
        source_texts = self.source_text_index(requirements)
        for block in content.get("blocks", []):
            block_id = block.get("block_id", "unknown")
            text = self.content_text(block)
            if not text:
                continue
            source_ids = block.get("source_requirement_ids", []) + block.get("scoring_item_ids", []) + block.get("writing_requirement_ids", [])
            source_text = " ".join(source_texts.get(source_id, "") for source_id in source_ids)
            high_risk_flags = self.high_risk_flags(text)
            risk_flags = set(block.get("risk_flags", []))
            if high_risk_flags and block.get("status") not in REVIEW_STATUSES | CONFIRM_STATUSES:
                if not source_ids or not self.source_supports_risk(source_text, high_risk_flags):
                    issue_count += 1
                    self.add_issue(
                        "blocking",
                        "unsupported_claim",
                        f"内容块 {block_id} 出现高风险事实表达，但没有足够来源或复核/确认状态：{', '.join(sorted(high_risk_flags))}。",
                        "content-blocks.json",
                        block_id,
                        "Content Agent",
                        "补充来源 ID，或改为 REVIEW/CONFIRM 并进入对应清单。",
                    )
            if "unsupported_claim" in risk_flags:
                issue_count += 1
                self.add_issue(
                    "blocking",
                    "unsupported_claim",
                    f"内容块 {block_id} 已标记 unsupported_claim。",
                    "content-blocks.json",
                    block_id,
                    "Content Agent",
                    "删除无依据事实，或补充可追溯来源并重新生成内容块。",
                )
            if any(pattern in text for pattern in OVERCOMMITMENT_PATTERNS) and block.get("status") not in REVIEW_STATUSES | CONFIRM_STATUSES:
                if not source_text or not any(pattern in source_text for pattern in OVERCOMMITMENT_PATTERNS):
                    issue_count += 1
                    self.add_issue(
                        "blocking",
                        "overcommitment",
                        f"内容块 {block_id} 使用确定性承诺表达，但来源资料未提供同等依据。",
                        "content-blocks.json",
                        block_id,
                        "Content Agent",
                        "改写为有边界的响应描述，或保留 REVIEW/CONFIRM。",
                    )

        self.add_gate("虚构事实与过度承诺", "failed" if issue_count else "passed", "未发现未留痕的高风险事实或过度承诺。" if not issue_count else f"发现 {issue_count} 个风险事实问题。")

    def check_target_content_quality(self, content: dict[str, Any]) -> None:
        issue_count = 0
        for block in content.get("blocks", []):
            placeholder = block.get("placeholder")
            if placeholder not in TARGET_PLACEHOLDERS:
                continue
            text = self.content_text(block)
            leaks = [pattern for pattern in PROCESS_LEAK_PATTERNS if pattern in text]
            if leaks:
                issue_count += 1
                self.add_issue(
                    "blocking",
                    "process_language_leak",
                    f"目标章节内容块 {block.get('block_id')} 出现生成过程语言：{', '.join(leaks)}。",
                    "content-blocks.json",
                    block.get("block_id", "unknown"),
                    "Content Agent",
                    "重新生成系统架构与功能设计正文，正文中不得出现内部过程词。",
                )
        self.add_gate("目标章节过程语言检查", "failed" if issue_count else "passed", "系统架构和功能设计未发现过程语言。" if not issue_count else f"发现 {issue_count} 个过程语言问题。")

    def check_diagrams(self, design: dict[str, Any], content: dict[str, Any], diagrams: dict[str, Any]) -> None:
        issue_count = 0
        diagram_index = {diagram.get("diagram_id"): diagram for diagram in diagrams.get("diagrams", [])}
        design_plan_index = {item.get("diagram_id"): item for item in design.get("diagram_plan", [])}

        planned_architecture = [
            item for item in design.get("diagram_plan", []) if item.get("kind") == "architecture" and "架构" in str(item.get("title", ""))
        ]
        planned_function_flows = [item for item in design.get("diagram_plan", []) if item.get("kind") == "function_flow"]
        rendered_architecture = [
            diagram for diagram in diagrams.get("diagrams", []) if diagram.get("kind") == "architecture" and "架构" in str(diagram.get("title", ""))
        ]
        rendered_function_flows = [diagram for diagram in diagrams.get("diagrams", []) if diagram.get("kind") == "function_flow"]
        if not planned_architecture or not rendered_architecture:
            issue_count += 1
            self.add_issue("blocking", "diagram_text_mismatch", "缺少系统总体架构图。", "diagram-manifest.json", "architecture", "Design Agent", "补充系统总体架构图计划并重新生成 Mermaid 图表。")
        if len(planned_function_flows) < MIN_PRIMARY_FUNCTION_FLOW_DIAGRAMS or len(rendered_function_flows) < MIN_PRIMARY_FUNCTION_FLOW_DIAGRAMS:
            issue_count += 1
            self.add_issue(
                "blocking",
                "diagram_text_mismatch",
                f"一级功能流程图不足：计划 {len(planned_function_flows)}，已渲染 {len(rendered_function_flows)}，要求至少 {MIN_PRIMARY_FUNCTION_FLOW_DIAGRAMS}。",
                "diagram-manifest.json",
                "function_flow",
                "Design Agent",
                "按技术要求一级功能补齐每个功能的流程图。",
            )

        for block in content.get("blocks", []):
            block_req_ids = set(block.get("source_requirement_ids", []))
            for diagram_id in block.get("diagram_ids", []):
                diagram = diagram_index.get(diagram_id)
                if not diagram:
                    if diagram_id in design_plan_index:
                        continue
                    issue_count += 1
                    self.add_issue("blocking", "diagram_text_mismatch", f"内容块 {block.get('block_id')} 引用了不存在的图表 {diagram_id}。", "content-blocks.json", block.get("block_id", "unknown"), "Content Agent", "修正 diagram_ids 或补充图表 manifest。")
                    continue
                diagram_req_ids = set(diagram.get("source_requirement_ids", []))
                if block_req_ids and diagram_req_ids and not (block_req_ids & diagram_req_ids):
                    issue_count += 1
                    self.add_issue("blocking", "diagram_text_mismatch", f"内容块 {block.get('block_id')} 与图表 {diagram_id} 的来源需求没有交集。", "content-blocks.json", block.get("block_id", "unknown"), "Content Agent", "调整正文或图表来源 ID，使图文依据一致。")
                if diagram.get("assembly_allowed") is not True:
                    issue_count += 1
                    self.add_issue("blocking", "diagram_text_mismatch", f"内容块 {block.get('block_id')} 引用了不可装配图表 {diagram_id}。", "diagram-manifest.json", diagram_id, "Render Validate Agent", "修复图表渲染结果，或从正文引用中移除该图表。")

        for diagram_id, diagram in diagram_index.items():
            render_status = diagram.get("render_status")
            if render_status == "fallback_rendered":
                issue_count += 1
                self.add_issue(
                    "blocking",
                    "fallback_render_unmarked",
                    f"图表 {diagram_id} 为 fallback_rendered，目标图表不得以降级 PNG 通过。",
                    "diagram-manifest.json",
                    diagram_id,
                    "Render Validate Agent",
                    "安装或指定 Mermaid CLI，修复 Mermaid 源码并完成原生渲染。",
                )
            if render_status in {"failed", "skipped"} or diagram.get("assembly_allowed") is not True:
                issue_count += 1
                self.add_issue("blocking", "diagram_text_mismatch", f"图表 {diagram_id} 渲染状态为 {render_status}，不允许装配。", "diagram-manifest.json", diagram_id, "Render Validate Agent", "修复 Mermaid 源码或渲染流程后重新发布图表。")
            image_check = diagram.get("image_check", {})
            if image_check.get("exists") is False or image_check.get("blank_risk") == "high":
                issue_count += 1
                self.add_issue("blocking", "diagram_text_mismatch", f"图表 {diagram_id} 图片检查失败或空白风险高。", "diagram-manifest.json", diagram_id, "Render Validate Agent", "重新渲染并检查 PNG 文件。")
            plan = design_plan_index.get(diagram_id)
            if plan:
                plan_req_ids = set(plan.get("source_requirement_ids", []))
                diagram_req_ids = set(diagram.get("source_requirement_ids", []))
                if plan_req_ids and diagram_req_ids and not (plan_req_ids & diagram_req_ids):
                    issue_count += 1
                    self.add_issue("blocking", "diagram_text_mismatch", f"图表 {diagram_id} 与设计蓝图中的来源需求不一致。", "diagram-manifest.json", diagram_id, "Mermaid Agent", "按 design-blueprint.diagram_plan 修正图表来源。")

        self.add_gate("Mermaid 降级渲染检查", "failed" if any(issue["category"] == "fallback_render_unmarked" for issue in self.issues) else "passed", "未发现 Mermaid 降级渲染。" if not any(issue["category"] == "fallback_render_unmarked" for issue in self.issues) else "存在 fallback_rendered 图表，已阻断。")
        self.add_gate("图文一致性", "failed" if issue_count else "passed", "图表与正文、设计蓝图来源一致。" if not issue_count else f"发现 {issue_count} 个图文或渲染问题。")

    def check_placeholder_log(self) -> None:
        placeholder_log = self.records_dir / "placeholder-fill-log.md"
        issue_count = 0
        if placeholder_log.exists():
            text = placeholder_log.read_text(encoding="utf-8", errors="replace")
            suspicious_lines = [
                line.strip()
                for line in text.splitlines()
                if any(pattern in line for pattern in ("unresolved", "未处理", "未解释", "残留", "failed"))
            ]
            for index, line in enumerate(suspicious_lines[:10], 1):
                issue_count += 1
                self.add_issue(
                    "blocking",
                    "placeholder_unresolved",
                    f"占位符填充日志存在未处理记录：{line}",
                    "placeholder-fill-log.md",
                    f"line-{index}",
                    "Word Layout Agent",
                    "修复占位符填充记录后重新运行 Review Gate。",
                )
            self.add_gate("占位符残留检查", "failed" if issue_count else "passed", "placeholder-fill-log.md 未显示未处理占位符。" if not issue_count else f"发现 {issue_count} 条占位符残留记录。")
        else:
            self.add_gate("占位符残留检查", "warning", "未提供 placeholder-fill-log.md；本次仅检查内容块中的占位符。")

    def build_inputs(self, artifacts: dict[str, ArtifactLoad]) -> list[dict[str, str]]:
        inputs = []
        for key, artifact in artifacts.items():
            if not artifact.data:
                continue
            inputs.append(
                {
                    "artifact": key,
                    "path": self.relative(artifact.path),
                    "schema_version": str(artifact.data.get("schema_version", "unknown")),
                }
            )
        placeholder_log = self.records_dir / "placeholder-fill-log.md"
        if placeholder_log.exists():
            inputs.append({"artifact": "placeholder-fill-log", "path": self.relative(placeholder_log), "schema_version": "n/a"})
        return inputs

    def build_checklist(self, content: dict[str, Any], field: str, prefix: str) -> list[dict[str, Any]]:
        items = []
        for index, item in enumerate(content.get(field, []), 1):
            block_id = item.get("block_id", "unknown")
            status = "open" if item.get("status") not in CLOSED_ITEM_STATUSES else item.get("status")
            items.append(
                {
                    "item_id": item.get("item_id") or f"{prefix}{index:03d}",
                    "title": item.get("message") or block_id,
                    "source": {"artifact": "content-blocks.json", "id": block_id},
                    "status": status,
                    "note": ", ".join(item.get("source_ids", [])) if item.get("source_ids") else "",
                }
            )
        return items

    def next_actions(self) -> list[str]:
        if not self.issues:
            return ["Review Gate 已通过，可以进入 Word Layout Agent。"]
        owners = list(dict.fromkeys(issue["owner_agent"] for issue in self.issues if issue["blocking"]))
        if not owners:
            return ["处理 Review Gate warning 后可继续。"]
        return [f"将阻断问题退回 {owner} 处理。" for owner in owners]

    def validate_release_decision(self, decision: dict[str, Any]) -> None:
        required = [
            "schema_version",
            "artifact",
            "run_id",
            "generated_at",
            "producer",
            "inputs",
            "decision",
            "allow_word_assembly",
            "quality_gates",
            "issues",
            "confirm_items",
            "review_items",
            "coverage_summary",
        ]
        missing = [field for field in required if field not in decision]
        if missing:
            raise RuntimeError(f"release-decision.json 缺少字段：{', '.join(missing)}")
        if decision["decision"] == "approved" and decision["allow_word_assembly"] is not True:
            raise RuntimeError("approved 决策必须 allow_word_assembly=true")
        if decision["decision"] == "blocked" and decision["allow_word_assembly"] is not False:
            raise RuntimeError("blocked 决策必须 allow_word_assembly=false")
        if any(issue["severity"] == "blocking" for issue in decision["issues"]) and decision["decision"] != "blocked":
            raise RuntimeError("存在 blocking issue 时必须 blocked")
        summary = decision["coverage_summary"]
        if summary["requirements_covered"] + summary["requirements_uncovered"] != summary["requirements_total"]:
            raise RuntimeError("coverage_summary 需求覆盖计数不一致")
        if summary.get("writing_requirements_covered", 0) + summary.get("writing_requirements_uncovered", 0) != summary.get("writing_requirements_total", 0):
            raise RuntimeError("coverage_summary 方案撰写要求覆盖计数不一致")
        if summary["scoring_items_covered"] > summary["scoring_items_total"]:
            raise RuntimeError("coverage_summary 评分项覆盖计数不一致")

    def render_review_report(self, decision: dict[str, Any]) -> str:
        lines = [
            "# Review Gate Report",
            "",
            f"- Run ID: `{decision['run_id']}`",
            f"- Generated At: `{decision['generated_at']}`",
            f"- Decision: `{decision['decision']}`",
            f"- Allow Word Assembly: `{str(decision['allow_word_assembly']).lower()}`",
            "",
            "## Quality Gates",
            "",
            "| Gate | Status | Message |",
            "|---|---|---|",
        ]
        for gate in decision["quality_gates"]:
            lines.append(f"| {gate['name']} | {gate['status']} | {self.escape_md(gate.get('message', ''))} |")
        lines.extend(["", "## Issues", ""])
        if not decision["issues"]:
            lines.extend(["未发现阻断问题。", ""])
        else:
            lines.extend(["| ID | Severity | Category | Source | Owner | Suggested Action |", "|---|---|---|---|---|---|"])
            for issue in decision["issues"]:
                source = f"{issue['source']['artifact']}#{issue['source']['id']}"
                lines.append(
                    f"| {issue['issue_id']} | {issue['severity']} | {issue['category']} | {self.escape_md(source)} | {issue['owner_agent']} | {self.escape_md(issue['suggested_action'])} |"
                )
        lines.extend(["", "## Next Actions", ""])
        lines.extend(f"- {item}" for item in decision.get("next_actions", []))
        lines.append("")
        return "\n".join(lines)

    def render_coverage_check(self, decision: dict[str, Any]) -> str:
        summary = decision["coverage_summary"]
        lines = [
            "# Coverage Check",
            "",
            f"- Requirements: {summary['requirements_covered']}/{summary['requirements_total']} covered",
            f"- Requirements uncovered: {summary['requirements_uncovered']}",
            f"- Writing requirements: {summary.get('writing_requirements_covered', 0)}/{summary.get('writing_requirements_total', 0)} expanded",
            f"- Writing requirements uncovered: {summary.get('writing_requirements_uncovered', 0)}",
            f"- Scoring items: {summary['scoring_items_covered']}/{summary['scoring_items_total']} covered",
            f"- Diagrams assembly allowed: {summary.get('diagram_assembly_allowed', 0)}/{summary.get('diagram_total', 0)}",
            "",
            "## Coverage Issues",
            "",
        ]
        coverage_issues = [
            issue
            for issue in decision["issues"]
            if issue["category"] in {"uncovered_requirement", "uncovered_writing_requirement", "unanswered_scoring_item", "missing_source_id"}
        ]
        if not coverage_issues:
            lines.extend(["未发现覆盖率或来源 ID 问题。", ""])
            return "\n".join(lines)
        lines.extend(["| ID | Category | Source | Message |", "|---|---|---|---|"])
        for issue in coverage_issues:
            source = f"{issue['source']['artifact']}#{issue['source']['id']}"
            lines.append(f"| {issue['issue_id']} | {issue['category']} | {self.escape_md(source)} | {self.escape_md(issue['message'])} |")
        lines.append("")
        return "\n".join(lines)

    def render_checklist(self, title: str, items: list[dict[str, Any]]) -> str:
        lines = ["# " + title, "", f"- Run ID: `{self.run_id}`", f"- Generated At: `{self.generated_at}`", ""]
        if not items:
            lines.extend(["本次未登记相关事项。", ""])
            return "\n".join(lines)
        lines.extend(["| ID | Title | Source | Status | Note |", "|---|---|---|---|---|"])
        for item in items:
            source = f"{item['source']['artifact']}#{item['source']['id']}"
            lines.append(f"| {item['item_id']} | {self.escape_md(item['title'])} | {self.escape_md(source)} | {item['status']} | {self.escape_md(item.get('note', ''))} |")
        lines.append("")
        return "\n".join(lines)

    def add_gate(self, name: str, status: str, message: str) -> None:
        self.gates.append({"gate_id": f"G{self.gate_index:03d}", "name": name, "status": status, "message": message})
        self.gate_index += 1

    def add_issue(
        self,
        severity: str,
        category: str,
        message: str,
        artifact: str,
        source_id: str,
        owner_agent: str,
        suggested_action: str,
    ) -> None:
        self.issues.append(
            {
                "issue_id": f"RI{self.issue_index:03d}",
                "severity": severity,
                "category": category,
                "message": message,
                "source": {"artifact": self.source_artifact_name(artifact), "id": source_id},
                "owner_agent": owner_agent,
                "suggested_action": suggested_action,
                "blocking": severity == "blocking",
            }
        )
        self.issue_index += 1

    @staticmethod
    def source_artifact_name(value: str) -> str:
        if value in {
            "requirements.json",
            "requirements-matrix.json",
            "design-blueprint.json",
            "content-blocks.json",
            "diagram-specs.json",
            "diagram-manifest.json",
            "placeholder-fill-log.md",
        }:
            return value
        if value.endswith(".json") or value.endswith(".md"):
            return value
        return EXPECTED_JSON_ARTIFACTS.get(value, "requirements.json")

    @staticmethod
    def owner_for_artifact(key: str) -> str:
        return {
            "requirements": "Requirement Evidence Agent",
            "requirements-matrix": "Requirement Evidence Agent",
            "design-blueprint": "Design Agent",
            "content-blocks": "Content Agent",
            "diagram-manifest": "Render Validate Agent",
        }.get(key, "Coordinator Agent")

    @staticmethod
    def find_by_id(items: list[dict[str, Any]], field: str, value: str) -> dict[str, Any]:
        for item in items:
            if item.get(field) == value:
                return item
        return {}

    @staticmethod
    def ids_from_blocks(content: dict[str, Any], field: str) -> set[str]:
        return {source_id for block in content.get("blocks", []) for source_id in block.get(field, [])}

    @staticmethod
    def ids_from_lifecycle(content: dict[str, Any]) -> set[str]:
        return {
            source_id
            for field in ("confirm_items", "review_items")
            for item in content.get(field, [])
            for source_id in item.get("source_ids", [])
        }

    @staticmethod
    def lifecycle_has_any_source(items: list[dict[str, Any]], source_ids: set[str]) -> bool:
        return any(source_ids & set(item.get("source_ids", [])) for item in items)

    @staticmethod
    def content_text(block: dict[str, Any]) -> str:
        content = block.get("content")
        if isinstance(content, list):
            return " ".join(str(item) for item in content)
        if isinstance(content, dict):
            parts: list[str] = []
            for value in content.values():
                if isinstance(value, list):
                    parts.extend(str(item) for item in value)
                else:
                    parts.append(str(value))
            return " ".join(parts)
        return str(content or "")

    @staticmethod
    def high_risk_flags(text: str) -> set[str]:
        return {flag for flag, keywords in HIGH_RISK_KEYWORDS.items() if any(keyword in text for keyword in keywords)}

    @staticmethod
    def source_supports_risk(source_text: str, flags: set[str]) -> bool:
        return all(any(keyword in source_text for keyword in HIGH_RISK_KEYWORDS[flag]) for flag in flags)

    @staticmethod
    def source_text_index(requirements: dict[str, Any]) -> dict[str, str]:
        index: dict[str, str] = {}
        for item in requirements.get("requirements", []):
            index[item.get("requirement_id", "")] = " ".join(
                str(part)
                for part in (
                    item.get("title", ""),
                    item.get("text", ""),
                    item.get("source", {}).get("quote", ""),
                    " ".join(item.get("keywords", [])),
                )
            )
        for item in requirements.get("delivery_items", []):
            index[item.get("delivery_id", "")] = " ".join(
                str(part)
                for part in (
                    item.get("name", ""),
                    item.get("quantity", ""),
                    item.get("medium", ""),
                    item.get("source", {}).get("quote", ""),
                )
            )
        for item in requirements.get("scoring_items", []):
            index[item.get("scoring_item_id", "")] = " ".join(
                str(part)
                for part in (
                    item.get("title", ""),
                    item.get("text", ""),
                    item.get("source", {}).get("quote", ""),
                )
            )
        for item in requirements.get("writing_requirements", []):
            index[item.get("writing_requirement_id", "")] = " ".join(
                str(part)
                for part in (
                    item.get("title", ""),
                    item.get("text", ""),
                    item.get("source", {}).get("quote", ""),
                    " ".join(item.get("keywords", [])),
                )
            )
        return index

    @staticmethod
    def escape_md(value: str) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace.resolve())).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Review Gate Agent.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root.")
    parser.add_argument("--records-dir", type=Path, default=Path("output/records"), help="Directory containing published artifacts.")
    parser.add_argument("--output-dir", type=Path, default=Path("output/records"), help="Published record output directory.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    workspace = args.workspace.resolve()
    records_dir = args.records_dir if args.records_dir.is_absolute() else workspace / args.records_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else workspace / args.output_dir

    agent = ReviewGateAgent(workspace=workspace, records_dir=records_dir, output_dir=output_dir)
    paths = agent.run()
    decision = paths["decision"]
    print(f"Review Gate Agent completed: {agent.run_id}")
    print(f"decision: {decision}")
    print(f"staging: {paths['staging_dir']}")
    print(f"published: {paths['published_dir']}")
    print(f"output: {paths['output_dir']}")
    return 0 if decision == "approved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
