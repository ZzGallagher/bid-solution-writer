# Agent 数据契约

本文定义投标方案生成多 Agent 流水线的中间产物契约。契约以 `docs/agent-system-design.md` 为依据，目标是让各 Agent 按稳定字段协作，而不是各自解释上游文件。

## 总原则

- `requirements.json` 是事实源，所有正文、图表、审核问题和装配记录必须回溯到需求 ID、评分项 ID 或人工确认项。
- 所有 JSON 产物必须包含 `schema_version`、`artifact`、`run_id`、`generated_at`、`producer`、`inputs`。
- 所有 Agent 先写入 `working/agent-system/staging/<stage>/`，校验通过后由 Coordinator 发布到 `working/agent-system/published/<stage>/` 或 `output/records/`。
- `CONFIRM` 信息不得自动补齐；`REVIEW` 内容可以生成初稿，但必须进入复核清单。
- Review Gate 是 Word 装配前唯一放行点，Word Layout Agent 只消费 `decision=approved` 且 `allow_word_assembly=true` 的产物。

## 产物链路

| 顺序 | 产物 | 责任 Agent | Schema |
|---|---|---|---|
| 1 | `run-manifest.json` | Coordinator Agent | `schemas/run-manifest.schema.json` |
| 2 | `requirements.json` | Requirement Evidence Agent | `schemas/requirements.schema.json` |
| 3 | `requirements-matrix.json` | Requirement Evidence Agent | `schemas/requirements-matrix.schema.json` |
| 4 | `design-blueprint.json` | Design Agent | `schemas/design-blueprint.schema.json` |
| 5 | `content-blocks.json` | Content Agent | `schemas/content-blocks.schema.json` |
| 6 | `diagram-specs.json` | Mermaid Agent | `schemas/diagram-specs.schema.json` |
| 7 | `diagram-manifest.json` | Render Validate Agent | `schemas/diagram-manifest.schema.json` |
| 8 | `release-decision.json` | Review Gate Agent | `schemas/release-decision.schema.json` |
| 9 | `assembly-manifest.json` | Word Layout Agent | `schemas/assembly-manifest.schema.json` |

`requirements-matrix.md`、`review-report.md`、`coverage-check.md`、`人工确认清单.md`、`复核清单.md` 是面向人工阅读的派生报告，不作为机器事实源。

## ID 规则

| 对象 | 格式 | 示例 |
|---|---|---|
| 运行 | `RUN-YYYYMMDD-HHMMSS` | `RUN-20260530-210000` |
| 技术功能需求 | `TNNN` | `T001` |
| 性能需求 | `PNNN` | `P001` |
| 质量需求 | `QNNN` | `Q001` |
| 商务需求 | `BNNN` | `B001` |
| 交付需求 | `DNNN` | `D001` |
| 评分项 | `SNNN` | `S001` |
| 架构层 | `LNNN` | `L001` |
| 模块 | `MNNN` | `M001` |
| 章节 | `SECNNN` | `SEC001` |
| 内容块 | `CBNNN` | `CB001` |
| 图表 | `DGNNN` | `DG001` |
| 审核问题 | `RINNN` | `RI001` |

图表统一使用 `DGNNN`，避免与交付需求 `DNNN` 冲突。设计文档早期示例中的图表 `D001` 在新契约中迁移为 `DG001`。

## 状态机

| 阶段 | 状态 |
|---|---|
| 抽取与计划 | `extracted`、`planned` |
| 生成与审核 | `generated`、`review_required`、`confirm_required` |
| 发布与阻断 | `approved`、`blocked`、`published`、`failed` |

`status` 表达产物或条目状态；是否允许 Word 装配只看 `release-decision.json`。

## 高风险事实

以下事实没有明确输入依据时，不允许写成确定事实：人员、资质、业绩、报价、质保期、交付周期、服务响应时限、驻场安排、品牌、型号、供应商承诺。

处理规则：

- 不确定但必须人工补充的内容进入 `confirm_required`。
- 可以先形成草稿但必须复核的内容进入 `review_required`。
- 所有 `confirm_required` 和 `review_required` 必须在 `release-decision.json` 中保留清单项。

## 旧结构迁移说明

旧 `output/records/requirements.json` 使用 `function_requirements`、`performance_requirements`、`quality_requirements`、`business_requirements`、`scoring_items` 分组。新契约迁移为：

| 旧字段 | 新字段 |
|---|---|
| `function_requirements[]` | `requirements[]` 且 `category=technical_function` |
| `performance_requirements[]` | `requirements[]` 且 `category=technical_performance` |
| `quality_requirements[]` | `requirements[]` 且 `category=technical_quality` |
| `business_requirements[]` | `requirements[]` 且 `category=business` |
| `delivery_rows[]` | `delivery_items[]` 与 `requirements[]` 中的 `category=delivery` |
| `scoring_items[]` | `scoring_items[]`，字段名规范为 `scoring_item_id` |

迁移时不得改变原文含义；无法定位来源的条目必须生成 extraction warning。
