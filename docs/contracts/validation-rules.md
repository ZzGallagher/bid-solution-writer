# 数据契约校验规则

JSON Schema 只校验单文件结构。以下规则需要由 Review Gate、Coordinator 或独立校验器执行。

## 跨文件引用

- 所有 `requirement_ids` 必须存在于 `requirements.json.requirements[].requirement_id` 或 `requirements.json.delivery_items[].delivery_id`。
- 所有 `scoring_item_ids` 必须存在于 `requirements.json.scoring_items[].scoring_item_id`。
- 所有 `section_ids` 必须存在于 `design-blueprint.json.sections[].section_id`。
- 所有 `module_ids` 必须存在于 `design-blueprint.json.modules[].module_id`。
- 所有 `diagram_ids` 必须存在于 `diagram-specs.json.diagrams[].diagram_id`，并在 `diagram-manifest.json.diagrams[].diagram_id` 中有渲染记录。
- 所有 `block_ids` 必须存在于 `content-blocks.json.blocks[].block_id`。

## Release Gate

- `decision=approved` 时，`allow_word_assembly` 必须为 `true`。
- `decision=blocked` 时，`allow_word_assembly` 必须为 `false`。
- 存在 `severity=blocking` 的 issue 时，`decision` 必须为 `blocked`。
- `requirements_covered + requirements_uncovered` 必须等于 `requirements_total`。
- `scoring_items_covered` 不得大于 `scoring_items_total`。
- 任何 `confirm_required` 未关闭时，不阻止初稿生成，但必须出现在 `confirm_items`，并在 Word 装配中保留占位符。
- 任何 `review_required` 未关闭时，必须出现在 `review_items`。

## 图表与正文一致性

- `content-blocks.json` 中引用的图表必须在 `diagram-manifest.json` 中 `assembly_allowed=true`。
- `render_status=fallback_rendered` 的图必须有 Review Gate issue 或 gate message 说明是否允许装配。
- `render_status=failed` 或 `skipped` 的图不得进入 Word 装配。
- 图表说明中的 `source_requirement_ids` 必须与对应正文块或章节存在交集；没有交集时必须生成 `diagram_text_mismatch` issue。

## 事实与风险

- Content 或 Diagram 产物不得只用自然语言描述来源，必须绑定需求 ID 或评分项 ID。
- 出现人员、资质、业绩、报价、质保期、交付周期、服务承诺等高风险事实时，若来源 ID 不能证明该事实，必须进入 `confirm_required` 或 `review_required`。
- 正文使用“保证”“完全满足”“无偏离”“固定响应时间”等确定性表达时，必须有输入证据；否则 Review Gate 生成 `overcommitment` issue。

## 发布与恢复

- 同一 `run_id` 的 staging 产物不得覆盖已发布产物。
- `run-manifest.json.stages[].status=published` 时，必须存在 `published_path`。
- `run-manifest.json.stages[].status=failed` 时，必须存在 `errors[]` 和可执行的 `recovery_action`。
- Word Layout Agent 只能读取已发布产物或 `output/records/` 产物，不得直接消费未校验的 staging 文件。
