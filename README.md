# 投标方案生成工具

本项目用于根据 `input/` 中的招标资料和 `templates/` 中的 Word 模板生成投标方案初稿。

## 技术路线

当前已完成的阶段入口：

- `working/agents/requirement_evidence_agent.py`：需求证据抽取。
- `working/agents/design_agent.py`：设计蓝图、模块、章节和图表计划。
- `working/agents/content_agent.py`：正文内容块生成。
- `working/agents/mermaid_agent.py`：根据图表计划生成 Mermaid 源码和 `diagram-specs.json`。
- `working/agents/render_validate_agent.py`：渲染校验 Mermaid，输出 PNG、渲染日志和 `diagram-manifest.json`。
- `working/agents/review_gate_agent.py`：发布前审核 Gate。
- `working/agents/word_layout_agent.py`：审核通过后复制 Word 模板、填充占位符并输出最终 `.docx` 与装配记录。

需求证据抽取阶段：

1. 读取 `input/技术要求.docx`、`input/商务要求.docx`、`input/技术评分表.docx`。
2. 抽取项目名称、技术标准、功能要求、性能要求、质量要求、商务条款、评分项和交付物表。
3. 将抽取结果写入 `output/records/requirements.json`。
4. 生成 `output/records/requirements-matrix.json` 和 `output/records/requirements-matrix.md`，建立“来源条款 -> 方案章节”的映射。
5. 输出 `output/records/confirm-candidates.md` 和 `output/records/extraction-warnings.md`。
6. 同一 run 的阶段产物先写入 `working/agent-system/staging/requirements/<run_id>/`，校验通过后发布到 `working/agent-system/published/requirements/<run_id>/` 和 `output/records/`。

Design Agent 阶段：

1. 读取 `output/records/requirements.json` 和 `requirements-matrix.json`。
2. 扫描 `templates/投标方案模板.docx` 中的 `GEN`、`REVIEW`、`CONFIRM` 占位符。
3. 规划系统分层、功能模块、章节结构、图表清单和需求 ID 覆盖映射。
4. 输出 `design-blueprint.json`、`section-plan.md`、`diagram-plan.json` 和 `diagram-plan.md`。
5. 同一 run 的阶段产物先写入 `working/agent-system/staging/design/<run_id>/`，校验通过后发布到 `working/agent-system/published/design/<run_id>/` 和 `output/records/`。

Content Agent 阶段：

1. 读取 `output/records/requirements.json`、`requirements-matrix.json`、`design-blueprint.json` 和 `section-plan.md`。
2. 通过统一入口 `working/agents/llm_client.py` 的 `call_llm_api(payload)` 生成 `GEN` / `REVIEW` 正文草稿。
3. 为每个内容块绑定需求 ID 或评分项 ID，保留 `CONFIRM` 占位符，并把 `REVIEW` / `CONFIRM` 事项写入清单。
4. 输出 `content-blocks.json`、`content-preview.md` 和 `content-review-notes.md`。
5. 同一 run 的阶段产物先写入 `working/agent-system/staging/content/<run_id>/`，校验通过后发布到 `working/agent-system/published/content/<run_id>/` 和 `output/records/`。

Mermaid Agent 阶段：

1. 读取 `output/records/requirements.json`、`design-blueprint.json` 和 `diagram-plan.json`。
2. 生产模式通过统一入口 `working/agents/llm_client.py` 的 `call_llm_api(payload)` 生成 Mermaid 源码。
3. 开发验收可使用 `--allow-local-draft` 生成确定性的本地图表草稿，不依赖外部 LLM 或网络。
4. 输出 `diagram-specs.json`、`diagram-descriptions.md` 和 `output/records/diagrams/*.mmd`。
5. 同一 run 的阶段产物先写入 `working/agent-system/staging/diagrams/<run_id>/`，校验通过后发布到 `working/agent-system/published/diagrams/<run_id>/` 和 `output/records/`。

Render Validate Agent 阶段：

1. 读取 `output/records/diagram-specs.json` 和对应 `output/records/diagrams/*.mmd`。
2. 优先调用 Mermaid CLI 兼容渲染器 `mmdc`，也可通过 `--renderer-command` 指定渲染器。
3. 对 Mermaid 源码做基础语法检查，记录原生渲染失败原因。
4. 渲染器缺失或失败时生成降级 PNG，并明确标记 `render_status=fallback_rendered`。
5. 检查 PNG 是否存在、尺寸、文件大小和空白图风险。
6. 输出 `diagram-manifest.json`、`diagram-render-log.md` 和 `output/records/diagrams/*.png`。
7. 同一 run 的阶段产物先写入 `working/agent-system/staging/diagrams/<run_id>/`，校验通过后发布到 `working/agent-system/published/diagrams/<run_id>/` 和 `output/records/`。

Review Gate 阶段：

1. 读取 `output/records/requirements.json`、`requirements-matrix.json`、`design-blueprint.json`、`content-blocks.json` 和 `diagram-manifest.json`。
2. 检查未覆盖需求、未响应评分项、虚构事实、`CONFIRM` 错误替换、`REVIEW` 未入清单、图文不一致、Mermaid 降级未标记和占位符残留。
3. 输出 `release-decision.json`、`review-report.md`、`coverage-check.md`、`人工确认清单.md` 和 `复核清单.md`。
4. 只有 `decision=approved` 且 `allow_word_assembly=true` 时，后续 Word Layout Agent 才允许继续。

Word Layout Agent 阶段：

1. 读取 `templates/投标方案模板.docx`、`content-blocks.json`、`diagram-manifest.json`、`requirements.json` 和 `release-decision.json`。
2. 严格检查 Review Gate 是否放行；未放行时只输出 blocked 装配记录，不生成最终 Word。
3. 替换 `COPY`、`GEN` 和 `REVIEW` 占位符，插入段落、表格和允许装配的图片，保留 `CONFIRM` 占位符。
4. 输出 `output/<项目名称>设计方案_V1.00_<YYYYMMDD>.docx`、`assembly-manifest.json`、`placeholder-fill-log.md`、`assembly-log.md` 和 `residual-placeholder-check.md`。
5. 同一 run 的阶段产物先写入 `working/agent-system/staging/assembly/<run_id>/`，校验通过后发布到 `working/agent-system/published/assembly/<run_id>/` 和 `output/records/`。

## 数据流

```text
input/*.docx
  -> Word 文本和表格抽取
  -> requirements.json
  -> requirements-matrix.json
  -> requirements-matrix.md
  -> design-blueprint.json
  -> section-plan.md
  -> diagram-plan.json
  -> diagram-specs.json
  -> diagram-manifest.json
  -> content-blocks.json
  -> content-preview.md
  -> content-review-notes.md
  -> confirm-candidates.md
  -> extraction-warnings.md
  -> release-decision.json
  -> assembly-manifest.json
  -> output/*.docx
```

## 占位符处理规则

- `COPY`：从 input 原文摘录或整理，不扩写事实。
- `GEN`：根据结构化需求、评分项和模板上下文生成方案正文。
- `REVIEW`：可以生成初稿，但进入复核清单。
- `CONFIRM`：保留在 Word 中，进入人工确认清单，不自动编造。

## 运行

```powershell
python working\agents\requirement_evidence_agent.py
```

运行 Design Agent：

```powershell
python working\agents\design_agent.py
```

运行 Content Agent：

```powershell
python working\agents\content_agent.py
```

首次使用前，只需要在 `working/agents/llm_client.py` 的 `call_llm_api(payload)` 中填写唯一的 LLM API 调用。未填写 API 时，可仅用于格式验证地运行：

```powershell
python working\agents\content_agent.py --allow-local-draft
```

运行 Mermaid Agent：

```powershell
python working\agents\mermaid_agent.py
```

同一个 `working/agents/llm_client.py` 入口也负责 Mermaid 生成。未填写 LLM API 时，可用于本地闭环验证：

```powershell
python working\agents\mermaid_agent.py --allow-local-draft
```

运行 Render Validate Agent：

```powershell
python working\agents\render_validate_agent.py
```

如果 Mermaid CLI 不在 `PATH` 中，可以指定渲染器：

```powershell
python working\agents\render_validate_agent.py --renderer-command C:\path\to\mmdc.cmd
```

运行 Review Gate：

```powershell
python working\agents\review_gate_agent.py
```

运行 Word Layout Agent：

```powershell
python working\agents\word_layout_agent.py
```

Word Layout Agent 只有在 `output/records/release-decision.json` 中 `decision=approved` 且 `allow_word_assembly=true` 时才会生成最终 `.docx`。如果审核未通过，它会返回退出码 `2` 并输出装配日志与 manifest，便于定位恢复点。

这些阶段仅使用 Python 标准库，不需要安装第三方依赖。

运行 Coordinator Agent：

```powershell
python working\agents\coordinator_agent.py
```

本地闭环全流程测试命令：

```powershell
python working\agents\coordinator_agent.py --allow-local-draft
```

该命令会依次生成并发布 `run-manifest.json`、`requirements.json`、`design-blueprint.json`、`content-blocks.json`、`diagram-specs.json`、`diagram-manifest.json`、`release-decision.json`、`assembly-manifest.json` 和最终 `.docx`。生产模式仍建议在 `working/agents/llm_client.py` 的 `call_llm_api()` 中接入真实 LLM 后运行不带 `--allow-local-draft` 的 Coordinator。

常用恢复与验证命令：

```powershell
# 只写 run-manifest 和 coordinator-log，不执行子 Agent
python working\agents\coordinator_agent.py --plan-only

# 从某个阶段恢复重跑
python working\agents\coordinator_agent.py --retry-from content --allow-local-draft

# 只运行单个阶段
python working\agents\coordinator_agent.py --only-stage review
```

Coordinator Agent 负责统一记录 `run-manifest.json`、阶段状态、staging/published 路径、失败重试和 Review Gate 阻断后的返回路由。它不会直接生成正文、Mermaid 语义或修改 Word 正文含义。
