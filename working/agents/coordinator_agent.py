#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Coordinator Agent.

This stage owns pipeline orchestration, run manifest management, phase status,
retry handling, and recovery routing. It does not generate proposal prose,
Mermaid business semantics, or Word content directly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


AGENT_NAME = "Coordinator Agent"
AGENT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"

STAGE_ORDER = ["requirements", "design", "content", "diagrams", "review", "assembly"]

STAGE_AGENTS = {
    "requirements": "Requirement Evidence Agent",
    "design": "Design Agent",
    "content": "Content Agent",
    "diagrams": "Mermaid Agent + Render Validate Agent",
    "review": "Review Gate Agent",
    "assembly": "Word Layout Agent",
}

STAGE_OUTPUTS = {
    "requirements": [
        "requirements.json",
        "requirements-matrix.json",
        "requirements-matrix.md",
        "confirm-candidates.md",
        "extraction-warnings.md",
    ],
    "design": [
        "design-blueprint.json",
        "section-plan.md",
        "diagram-plan.json",
        "diagram-plan.md",
    ],
    "content": [
        "content-blocks.json",
        "content-preview.md",
        "content-review-notes.md",
    ],
    "diagrams": [
        "diagram-specs.json",
        "diagram-descriptions.md",
        "diagram-manifest.json",
        "diagram-render-log.md",
    ],
    "review": [
        "release-decision.json",
        "review-report.md",
        "coverage-check.md",
        "人工确认清单.md",
        "复核清单.md",
    ],
    "assembly": [
        "assembly-manifest.json",
        "assembly-log.md",
        "placeholder-fill-log.md",
        "residual-placeholder-check.md",
    ],
}

MERMAID_OUTPUTS = ["diagram-specs.json", "diagram-descriptions.md"]
RENDER_OUTPUTS = ["diagram-manifest.json", "diagram-render-log.md"]

PRIMARY_ARTIFACT = {
    "requirements": "requirements.json",
    "design": "design-blueprint.json",
    "content": "content-blocks.json",
    "diagrams": "diagram-manifest.json",
    "review": "release-decision.json",
    "assembly": "assembly-manifest.json",
}

INPUT_FILES = [
    ("技术要求.docx", "technical_requirements", "input"),
    ("商务要求.docx", "business_requirements", "input"),
    ("技术评分表.docx", "scoring_table", "input"),
    ("投标方案模板.docx", "template", "template"),
]


@dataclass
class StageRecord:
    stage_id: str
    agent: str
    status: str
    staging_path: str
    published_path: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    errors: list[str] = field(default_factory=list)
    recovery_action: str = ""

    def to_manifest(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "stage_id": self.stage_id,
            "agent": self.agent,
            "status": self.status,
            "staging_path": self.staging_path,
        }
        if self.published_path:
            data["published_path"] = self.published_path
        if self.started_at:
            data["started_at"] = self.started_at
        if self.finished_at:
            data["finished_at"] = self.finished_at
        if self.errors:
            data["errors"] = self.errors
        if self.recovery_action:
            data["recovery_action"] = self.recovery_action
        return data


@dataclass
class StageResult:
    status: str
    returncode: int
    errors: list[str] = field(default_factory=list)
    recovery_action: str = ""


class CoordinatorAgent:
    def __init__(
        self,
        workspace: Path,
        input_dir: Path,
        template_dir: Path,
        records_dir: Path,
        output_dir: Path,
        start_stage: str,
        stop_stage: str,
        skip_stages: set[str],
        max_retries: int,
        allow_local_draft: bool,
        renderer_command: str | None,
        disable_fallback: bool,
        plan_only: bool,
    ) -> None:
        self.workspace = workspace
        self.input_dir = input_dir
        self.template_dir = template_dir
        self.records_dir = records_dir
        self.output_dir = output_dir
        self.start_stage = start_stage
        self.stop_stage = stop_stage
        self.skip_stages = skip_stages
        self.max_retries = max_retries
        self.allow_local_draft = allow_local_draft
        self.renderer_command = renderer_command
        self.disable_fallback = disable_fallback
        self.plan_only = plan_only

        now = datetime.now().astimezone()
        self.generated_at = now.isoformat(timespec="seconds")
        self.run_id = self.resolve_existing_run_id() or f"RUN-{now:%Y%m%d-%H%M%S}"
        self.stages: dict[str, StageRecord] = {}
        self.outputs: list[dict[str, str]] = []
        self.events: list[str] = []
        self.exit_status = "success"

    def run(self) -> int:
        self.create_base_dirs()
        self.initialize_stage_records()
        self.events.append(f"Coordinator started run {self.run_id}.")

        input_errors = self.check_inputs()
        if input_errors:
            self.mark_blocked_before_pipeline(input_errors)
            self.save_manifest()
            return 1

        selected_stages = self.selected_stages()
        if self.plan_only:
            for stage_id in selected_stages:
                if stage_id not in self.skip_stages:
                    self.stage(stage_id).status = "planned"
            self.events.append("Plan-only mode: no child agents were executed.")
            self.refresh_outputs()
            self.save_manifest()
            return 0

        for stage_id in selected_stages:
            if stage_id in self.skip_stages:
                record = self.stage(stage_id)
                record.status = "blocked"
                record.recovery_action = f"阶段 {stage_id} 被 --skip-stage 跳过；如需继续，请补齐该阶段产物后从后续阶段重跑。"
                self.events.append(record.recovery_action)
                self.save_manifest()
                continue

            result = self.run_stage_with_retries(stage_id)
            if result.status in {"failed", "blocked"}:
                self.exit_status = result.status
                self.save_manifest()
                return 2 if result.status == "blocked" else 1

        self.refresh_outputs()
        self.save_manifest()
        return 0

    def run_stage_with_retries(self, stage_id: str) -> StageResult:
        attempts = self.max_retries + 1
        last_result = StageResult("failed", 1, [f"{stage_id} 未执行。"])
        for attempt in range(1, attempts + 1):
            record = self.stage(stage_id)
            record.status = "generated"
            record.started_at = self.now()
            record.finished_at = None
            record.errors = []
            record.recovery_action = ""
            self.save_manifest()

            self.events.append(f"Running stage {stage_id}, attempt {attempt}/{attempts}.")
            last_result = self.run_stage(stage_id)
            record.finished_at = self.now()
            record.status = last_result.status
            record.errors = last_result.errors
            record.recovery_action = last_result.recovery_action

            if last_result.status == "published":
                self.sync_stage_publication(stage_id)
                record.status = "published"
                record.published_path = self.relative(self.published_path(stage_id))
                self.refresh_run_id_from_stage(stage_id)
                self.refresh_stage_paths()
                self.refresh_outputs()
                self.save_manifest()
                return last_result

            if last_result.status == "blocked":
                self.refresh_outputs()
                self.save_manifest()
                return last_result

            self.save_manifest()
            if attempt < attempts:
                self.events.append(f"Stage {stage_id} failed; retrying.")

        return last_result

    def run_stage(self, stage_id: str) -> StageResult:
        if stage_id == "requirements":
            return self.run_command(stage_id, self.requirements_command())
        if stage_id == "design":
            return self.run_command(stage_id, self.design_command())
        if stage_id == "content":
            return self.run_command(stage_id, self.content_command())
        if stage_id == "diagrams":
            mermaid = self.run_command(
                "diagrams",
                self.mermaid_command(),
                owner="Mermaid Agent",
                expected_outputs=MERMAID_OUTPUTS,
            )
            if mermaid.status != "published":
                return mermaid
            render = self.run_command(
                "diagrams",
                self.render_command(),
                owner="Render Validate Agent",
                expected_outputs=RENDER_OUTPUTS,
            )
            return render
        if stage_id == "review":
            result = self.run_command(stage_id, self.review_command(), allow_blocked_exit=True)
            return self.interpret_review_result(result)
        if stage_id == "assembly":
            result = self.run_command(stage_id, self.assembly_command(), allow_blocked_exit=True)
            return self.interpret_assembly_result(result)
        return StageResult("failed", 1, [f"未知阶段：{stage_id}"], "检查 Coordinator 阶段配置。")

    def run_command(
        self,
        stage_id: str,
        command: list[str],
        owner: str | None = None,
        allow_blocked_exit: bool = False,
        expected_outputs: list[str] | None = None,
    ) -> StageResult:
        try:
            completed = subprocess.run(
                command,
                cwd=self.workspace,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            message = f"{owner or STAGE_AGENTS[stage_id]} 无法启动：{exc}"
            return StageResult("failed", 1, [message], self.recovery_for_stage(stage_id, owner))

        self.record_command_output(stage_id, owner or STAGE_AGENTS[stage_id], completed)
        if completed.returncode == 0:
            missing = self.missing_outputs(stage_id, expected_outputs)
            if missing:
                return StageResult(
                    "failed",
                    completed.returncode,
                    [f"阶段产物缺失：{', '.join(missing)}"],
                    self.recovery_for_stage(stage_id, owner),
                )
            return StageResult("published", 0)

        if allow_blocked_exit and completed.returncode == 2:
            return StageResult("blocked", completed.returncode, self.command_errors(completed), self.recovery_for_stage(stage_id, owner))

        errors = self.command_errors(completed)
        return StageResult("failed", completed.returncode, errors, self.recovery_for_stage(stage_id, owner))

    def interpret_review_result(self, result: StageResult) -> StageResult:
        decision_path = self.records_dir / "release-decision.json"
        decision = self.load_json(decision_path)
        if decision and decision.get("decision") == "blocked":
            route = self.route_from_release_decision(decision)
            return StageResult("blocked", 2, result.errors, route)
        if decision and decision.get("decision") == "approved" and decision.get("allow_word_assembly") is True:
            return StageResult("published", 0)
        if result.status == "published":
            return result
        return StageResult("failed", result.returncode, result.errors, self.recovery_for_stage("review"))

    def interpret_assembly_result(self, result: StageResult) -> StageResult:
        manifest_path = self.records_dir / "assembly-manifest.json"
        manifest = self.load_json(manifest_path)
        if manifest:
            status = manifest.get("assembly_status")
            if status == "generated":
                return StageResult("published", 0)
            if status == "blocked":
                return StageResult(
                    "blocked",
                    2,
                    result.errors,
                    "Word Layout Agent 未执行装配，因为 Review Gate 未放行；请先处理 release-decision.json 中的阻断问题。",
                )
            if status == "failed":
                return StageResult(
                    "failed",
                    1,
                    result.errors or ["Word Layout Agent 装配失败。"],
                    "优先修复模板、图片路径或占位符装配记录，然后从 assembly 阶段重跑。",
                )
        return result

    def requirements_command(self) -> list[str]:
        return [
            sys.executable,
            str(self.workspace / "working" / "agents" / "requirement_evidence_agent.py"),
            "--workspace",
            str(self.workspace),
            "--input-dir",
            str(self.input_dir),
            "--template-dir",
            str(self.template_dir),
            "--output-dir",
            str(self.records_dir),
        ]

    def design_command(self) -> list[str]:
        return [
            sys.executable,
            str(self.workspace / "working" / "agents" / "design_agent.py"),
            "--workspace",
            str(self.workspace),
            "--records-dir",
            str(self.records_dir),
            "--template-dir",
            str(self.template_dir),
            "--output-dir",
            str(self.records_dir),
        ]

    def content_command(self) -> list[str]:
        command = [
            sys.executable,
            str(self.workspace / "working" / "agents" / "content_agent.py"),
            "--workspace",
            str(self.workspace),
            "--records-dir",
            str(self.records_dir),
            "--output-dir",
            str(self.records_dir),
        ]
        if self.allow_local_draft:
            command.append("--allow-local-draft")
        return command

    def mermaid_command(self) -> list[str]:
        command = [
            sys.executable,
            str(self.workspace / "working" / "agents" / "mermaid_agent.py"),
            "--workspace",
            str(self.workspace),
            "--records-dir",
            str(self.records_dir),
            "--output-dir",
            str(self.records_dir),
        ]
        if self.allow_local_draft:
            command.append("--allow-local-draft")
        return command

    def render_command(self) -> list[str]:
        command = [
            sys.executable,
            str(self.workspace / "working" / "agents" / "render_validate_agent.py"),
            "--workspace",
            str(self.workspace),
            "--records-dir",
            str(self.records_dir),
            "--output-dir",
            str(self.records_dir),
        ]
        if self.renderer_command:
            command.extend(["--renderer-command", self.renderer_command])
        if self.disable_fallback:
            command.append("--disable-fallback")
        return command

    def review_command(self) -> list[str]:
        return [
            sys.executable,
            str(self.workspace / "working" / "agents" / "review_gate_agent.py"),
            "--workspace",
            str(self.workspace),
            "--records-dir",
            str(self.records_dir),
            "--output-dir",
            str(self.records_dir),
        ]

    def assembly_command(self) -> list[str]:
        return [
            sys.executable,
            str(self.workspace / "working" / "agents" / "word_layout_agent.py"),
            "--workspace",
            str(self.workspace),
            "--records-dir",
            str(self.records_dir),
            "--template-path",
            str(self.template_dir / "投标方案模板.docx"),
            "--output-dir",
            str(self.output_dir),
            "--records-output-dir",
            str(self.records_dir),
        ]

    def selected_stages(self) -> list[str]:
        start_index = STAGE_ORDER.index(self.start_stage)
        stop_index = STAGE_ORDER.index(self.stop_stage)
        if stop_index < start_index:
            raise ValueError("--stop-stage 不能早于 --start-stage。")
        return STAGE_ORDER[start_index : stop_index + 1]

    def check_inputs(self) -> list[str]:
        errors = []
        for filename, _kind, location in INPUT_FILES:
            path = (self.template_dir if location == "template" else self.input_dir) / filename
            if not path.exists():
                errors.append(f"缺少必需输入文件：{self.relative(path)}")
        return errors

    def mark_blocked_before_pipeline(self, errors: list[str]) -> None:
        stage_id = self.start_stage
        record = self.stage(stage_id)
        record.status = "failed"
        record.started_at = self.now()
        record.finished_at = self.now()
        record.errors = errors
        record.recovery_action = "补齐 input/ 与 templates/ 下的必需文件后重新运行 Coordinator。"
        self.exit_status = "failed"
        self.events.extend(errors)

    def initialize_stage_records(self) -> None:
        for stage_id in STAGE_ORDER:
            self.stage(stage_id)

    def stage(self, stage_id: str) -> StageRecord:
        if stage_id not in self.stages:
            self.stages[stage_id] = StageRecord(
                stage_id=stage_id,
                agent=STAGE_AGENTS[stage_id],
                status="planned",
                staging_path=self.relative(self.staging_path(stage_id)),
                published_path=self.relative(self.published_path(stage_id)),
            )
        return self.stages[stage_id]

    def refresh_stage_paths(self) -> None:
        for stage_id, record in self.stages.items():
            record.staging_path = self.relative(self.staging_path(stage_id))
            record.published_path = self.relative(self.published_path(stage_id))

    def refresh_run_id_from_stage(self, stage_id: str) -> None:
        path = self.records_dir / PRIMARY_ARTIFACT[stage_id]
        data = self.load_json(path)
        if not data:
            return
        run_id = str(data.get("run_id") or "")
        if run_id and run_id != self.run_id:
            self.events.append(f"Run ID aligned from {self.run_id} to upstream artifact {run_id}.")
            self.run_id = run_id

    def resolve_existing_run_id(self) -> str | None:
        candidates = [
            self.records_dir / "requirements.json",
            self.records_dir / "release-decision.json",
            self.records_dir / "assembly-manifest.json",
        ]
        for path in candidates:
            data = self.load_json(path)
            if data and data.get("run_id"):
                return str(data["run_id"])
        return None

    def sync_stage_publication(self, stage_id: str) -> None:
        staging = self.staging_path(stage_id)
        published = self.published_path(stage_id)
        if staging.exists():
            self.copy_tree(staging, published)
        self.verify_output_records(stage_id)

    def verify_output_records(self, stage_id: str) -> None:
        missing = self.missing_outputs(stage_id)
        if missing:
            self.events.append(f"Stage {stage_id} output verification missing: {', '.join(missing)}.")

    def missing_outputs(self, stage_id: str, expected_outputs: list[str] | None = None) -> list[str]:
        if stage_id == "assembly":
            manifest = self.load_json(self.records_dir / "assembly-manifest.json")
            if manifest and manifest.get("assembly_status") in {"blocked", "failed"}:
                return []
        if stage_id == "review":
            decision = self.load_json(self.records_dir / "release-decision.json")
            if decision and decision.get("decision") == "blocked":
                return []
        names = expected_outputs if expected_outputs is not None else STAGE_OUTPUTS[stage_id]
        return [name for name in names if not (self.records_dir / name).exists()]

    def refresh_outputs(self) -> None:
        outputs: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for stage_id in STAGE_ORDER:
            for name in STAGE_OUTPUTS[stage_id]:
                path = self.records_dir / name
                if not path.exists():
                    continue
                artifact = Path(name).stem
                key = (artifact, self.relative(path))
                if key not in seen:
                    outputs.append({"artifact": artifact, "path": self.relative(path)})
                    seen.add(key)
        for docx in sorted(self.output_dir.glob("*.docx")):
            key = ("final-docx", self.relative(docx))
            if key not in seen:
                outputs.append({"artifact": "final-docx", "path": self.relative(docx)})
                seen.add(key)
        manifest_path = self.records_dir / "run-manifest.json"
        if manifest_path.exists():
            outputs.append({"artifact": "run-manifest", "path": self.relative(manifest_path)})
        self.outputs = outputs

    def route_from_release_decision(self, decision: dict[str, Any]) -> str:
        blocking = [issue for issue in decision.get("issues", []) if issue.get("blocking")]
        if not blocking:
            return "Review Gate blocked；请查看 output/records/review-report.md。"
        owners = list(dict.fromkeys(str(issue.get("owner_agent") or "Coordinator Agent") for issue in blocking))
        actions = []
        for owner in owners:
            stage = self.stage_for_owner(owner)
            actions.append(f"{owner} -> 从 {stage} 阶段重跑")
        return "Review Gate blocked；返回路由：" + "；".join(actions) + "。"

    @staticmethod
    def stage_for_owner(owner: str) -> str:
        if owner == "Requirement Evidence Agent":
            return "requirements"
        if owner == "Design Agent":
            return "design"
        if owner == "Content Agent":
            return "content"
        if owner in {"Mermaid Agent", "Render Validate Agent"}:
            return "diagrams"
        if owner == "Review Gate Agent":
            return "review"
        if owner == "Word Layout Agent":
            return "assembly"
        return "requirements"

    def recovery_for_stage(self, stage_id: str, owner: str | None = None) -> str:
        actual_owner = owner or STAGE_AGENTS[stage_id]
        if stage_id == "requirements":
            return "检查 input/*.docx 是否可读取，修复后从 requirements 阶段重跑。"
        if stage_id == "design":
            return "检查 requirements.json 与 requirements-matrix.json，修复后从 design 阶段重跑。"
        if stage_id == "content":
            return "检查 working/agents/llm_client.py 的统一 LLM 接口；验证阶段可使用 --allow-local-draft 后从 content 阶段重跑。"
        if stage_id == "diagrams":
            if actual_owner == "Mermaid Agent":
                return "填写 working/agents/llm_client.py 的统一 call_llm_api，或补齐 diagram-specs.json 后从 diagrams 阶段重跑。"
            return "检查 Mermaid 源码、渲染器或 fallback 设置后从 diagrams 阶段重跑。"
        if stage_id == "review":
            return "按 review-report.md 的 owner_agent 返回对应阶段修复，然后从该阶段重跑。"
        if stage_id == "assembly":
            return "检查 release-decision、模板、图片路径与占位符记录后从 assembly 阶段重跑。"
        return "查看 coordinator-log.md 后重跑失败阶段。"

    def create_base_dirs(self) -> None:
        for directory in [
            self.records_dir,
            self.output_dir,
            self.workspace / "working" / "agent-system" / "manifests",
        ]:
            directory.mkdir(parents=True, exist_ok=True)
        for stage_id in STAGE_ORDER:
            (self.workspace / "working" / "agent-system" / "staging" / stage_id).mkdir(parents=True, exist_ok=True)
            (self.workspace / "working" / "agent-system" / "published" / stage_id).mkdir(parents=True, exist_ok=True)

    def staging_path(self, stage_id: str) -> Path:
        return self.workspace / "working" / "agent-system" / "staging" / stage_id / self.run_id

    def published_path(self, stage_id: str) -> Path:
        return self.workspace / "working" / "agent-system" / "published" / stage_id / self.run_id

    def manifest_dir(self) -> Path:
        return self.workspace / "working" / "agent-system" / "manifests" / self.run_id

    def build_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "artifact": "run-manifest",
            "run_id": self.run_id,
            "generated_at": self.generated_at,
            "producer": {"agent": AGENT_NAME, "version": AGENT_VERSION},
            "inputs": self.build_inputs(),
            "stages": [self.stages[stage_id].to_manifest() for stage_id in STAGE_ORDER],
            "outputs": self.outputs,
            "recovery_pointer": self.recovery_pointer(),
        }

    def build_inputs(self) -> list[dict[str, str]]:
        inputs: list[dict[str, str]] = []
        for index, (filename, kind, location) in enumerate(INPUT_FILES, 1):
            path = (self.template_dir if location == "template" else self.input_dir) / filename
            item = {
                "input_id": f"IN{index:03d}",
                "path": self.relative(path),
                "kind": kind,
            }
            if path.exists():
                item["sha256"] = self.sha256(path)
            inputs.append(item)
        return inputs

    def recovery_pointer(self) -> str:
        for stage_id in STAGE_ORDER:
            record = self.stages[stage_id]
            if record.status in {"failed", "blocked"}:
                return record.recovery_action or f"从 {stage_id} 阶段重跑。"
        return "无阻断；可从任一后续阶段按需重跑。"

    def save_manifest(self) -> None:
        self.refresh_outputs()
        manifest = self.build_manifest()
        self.validate_manifest(manifest)
        text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
        log = self.render_log(manifest)

        self.manifest_dir().mkdir(parents=True, exist_ok=True)
        targets = [
            self.manifest_dir() / "run-manifest.json",
            self.workspace / "working" / "agent-system" / "run-manifest.json",
            self.records_dir / "run-manifest.json",
        ]
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

        log_targets = [
            self.manifest_dir() / "coordinator-log.md",
            self.workspace / "working" / "agent-system" / "coordinator-log.md",
            self.records_dir / "coordinator-log.md",
        ]
        for target in log_targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(log, encoding="utf-8")

    def validate_manifest(self, manifest: dict[str, Any]) -> None:
        required = ["schema_version", "artifact", "run_id", "generated_at", "producer", "inputs", "stages"]
        missing = [field for field in required if field not in manifest]
        if missing:
            raise RuntimeError(f"run-manifest.json 缺少字段：{', '.join(missing)}")
        if manifest["schema_version"] != SCHEMA_VERSION or manifest["artifact"] != "run-manifest":
            raise RuntimeError("run-manifest.json artifact 或 schema_version 不符合契约。")
        if manifest["producer"].get("agent") != AGENT_NAME:
            raise RuntimeError("run-manifest.json producer.agent 不符合契约。")
        for stage in manifest["stages"]:
            if stage["status"] == "published" and not stage.get("published_path"):
                raise RuntimeError(f"{stage['stage_id']} published 状态缺少 published_path。")
            if stage["status"] == "failed" and (not stage.get("errors") or not stage.get("recovery_action")):
                raise RuntimeError(f"{stage['stage_id']} failed 状态缺少 errors 或 recovery_action。")

    def render_log(self, manifest: dict[str, Any]) -> str:
        lines = [
            "# Coordinator Log",
            "",
            f"- Run ID: `{manifest['run_id']}`",
            f"- Generated At: `{manifest['generated_at']}`",
            f"- Exit Status: `{self.exit_status}`",
            "",
            "## Stage Status",
            "",
            "| Stage | Agent | Status | Recovery |",
            "|---|---|---|---|",
        ]
        for stage in manifest["stages"]:
            lines.append(
                "| {stage_id} | {agent} | {status} | {recovery} |".format(
                    stage_id=stage["stage_id"],
                    agent=stage["agent"],
                    status=stage["status"],
                    recovery=self.escape_md(stage.get("recovery_action") or "-"),
                )
            )
        lines.extend(["", "## Events", ""])
        if self.events:
            lines.extend(f"- {self.escape_md(event)}" for event in self.events)
        else:
            lines.append("- 无。")
        lines.extend(["", "## Outputs", ""])
        if manifest.get("outputs"):
            lines.extend(f"- {item['artifact']}: `{item['path']}`" for item in manifest["outputs"])
        else:
            lines.append("- 暂无已发布输出。")
        lines.append("")
        return "\n".join(lines)

    def record_command_output(self, stage_id: str, owner: str, completed: subprocess.CompletedProcess[str]) -> None:
        log_dir = self.manifest_dir() / "commands"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().strftime("%H%M%S")
        log_path = log_dir / f"{stage_id}-{stamp}.log"
        command_line = " ".join(self.quote_command_part(part) for part in completed.args)
        log_path.write_text(
            "\n".join(
                [
                    f"stage={stage_id}",
                    f"owner={owner}",
                    f"returncode={completed.returncode}",
                    f"command={command_line}",
                    "",
                    "## stdout",
                    completed.stdout or "",
                    "",
                    "## stderr",
                    completed.stderr or "",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.events.append(f"{owner} finished stage {stage_id} with code {completed.returncode}; log: {self.relative(log_path)}.")

    @staticmethod
    def command_errors(completed: subprocess.CompletedProcess[str]) -> list[str]:
        text = (completed.stderr or completed.stdout or "").strip()
        if not text:
            return [f"子进程退出码：{completed.returncode}"]
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return lines[-8:] or [f"子进程退出码：{completed.returncode}"]

    @staticmethod
    def copy_tree(source: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        for item in source.rglob("*"):
            relative = item.relative_to(source)
            destination = target / relative
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, destination)

    def load_json(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(self.workspace.resolve())).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    @staticmethod
    def quote_command_part(value: Any) -> str:
        text = str(value)
        if " " in text:
            return '"' + text.replace('"', '\\"') + '"'
        return text

    @staticmethod
    def now() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def escape_md(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Coordinator Agent.")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Workspace root.")
    parser.add_argument("--input-dir", type=Path, default=Path("input"), help="Directory containing tender inputs.")
    parser.add_argument("--template-dir", type=Path, default=Path("templates"), help="Directory containing Word templates.")
    parser.add_argument("--records-dir", type=Path, default=Path("output/records"), help="Published records directory.")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Final output directory.")
    parser.add_argument("--start-stage", choices=STAGE_ORDER, default="requirements", help="First stage to run.")
    parser.add_argument("--stop-stage", choices=STAGE_ORDER, default="assembly", help="Last stage to run.")
    parser.add_argument("--retry-from", choices=STAGE_ORDER, default=None, help="Alias for --start-stage when recovering.")
    parser.add_argument("--only-stage", choices=STAGE_ORDER, default=None, help="Run one stage only.")
    parser.add_argument("--skip-stage", action="append", choices=STAGE_ORDER, default=[], help="Mark a stage as skipped.")
    parser.add_argument("--max-retries", type=int, default=0, help="Retries per failed executable stage.")
    parser.add_argument(
        "--allow-local-draft",
        action="store_true",
        help="Pass through to Content and Mermaid agents for deterministic local draft verification.",
    )
    parser.add_argument("--renderer-command", default=None, help="Mermaid CLI-compatible renderer command.")
    parser.add_argument("--disable-fallback", action="store_true", help="Disable Render Validate fallback PNG generation.")
    parser.add_argument("--plan-only", action="store_true", help="Only validate inputs and write run manifest/log.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    workspace = args.workspace.resolve()
    input_dir = args.input_dir if args.input_dir.is_absolute() else workspace / args.input_dir
    template_dir = args.template_dir if args.template_dir.is_absolute() else workspace / args.template_dir
    records_dir = args.records_dir if args.records_dir.is_absolute() else workspace / args.records_dir
    output_dir = args.output_dir if args.output_dir.is_absolute() else workspace / args.output_dir

    start_stage = args.retry_from or args.start_stage
    stop_stage = args.stop_stage
    if args.only_stage:
        start_stage = args.only_stage
        stop_stage = args.only_stage

    agent = CoordinatorAgent(
        workspace=workspace,
        input_dir=input_dir,
        template_dir=template_dir,
        records_dir=records_dir,
        output_dir=output_dir,
        start_stage=start_stage,
        stop_stage=stop_stage,
        skip_stages=set(args.skip_stage),
        max_retries=max(0, args.max_retries),
        allow_local_draft=args.allow_local_draft,
        renderer_command=args.renderer_command,
        disable_fallback=args.disable_fallback,
        plan_only=args.plan_only,
    )
    exit_code = agent.run()
    print(f"Coordinator Agent completed: {agent.run_id}")
    print(f"status: {agent.exit_status}")
    print(f"manifest: {agent.records_dir / 'run-manifest.json'}")
    print(f"log: {agent.records_dir / 'coordinator-log.md'}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
