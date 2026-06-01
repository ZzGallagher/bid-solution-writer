# Review Gate Report

- Run ID: `RUN-20260601-163451`
- Generated At: `2026-06-01T16:44:23+08:00`
- Decision: `blocked`
- Allow Word Assembly: `false`

## Quality Gates

| Gate | Status | Message |
|---|---|---|
| 必需产物检查 | passed | 所有必需输入产物均可读取。 |
| 覆盖率与评分项响应 | passed | 需求覆盖 11/11，评分项响应 0/0，方案撰写要求扩写 19/19。 |
| 来源 ID 绑定 | passed | 所有正文与图表均有有效来源 ID。 |
| CONFIRM 与 REVIEW 留痕 | passed | 确认与复核事项已进入对应清单。 |
| 虚构事实与过度承诺 | passed | 未发现未留痕的高风险事实或过度承诺。 |
| 目标章节过程语言检查 | passed | 系统架构和功能设计未发现过程语言。 |
| Mermaid 降级渲染检查 | failed | 存在 fallback_rendered 图表，已阻断。 |
| 图文一致性 | failed | 发现 6 个图文或渲染问题。 |
| 占位符残留检查 | warning | 未提供 placeholder-fill-log.md；本次仅检查内容块中的占位符。 |

## Issues

| ID | Severity | Category | Source | Owner | Suggested Action |
|---|---|---|---|---|---|
| RI001 | blocking | fallback_render_unmarked | diagram-manifest.json#DG001 | Render Validate Agent | 安装或指定 Mermaid CLI，修复 Mermaid 源码并完成原生渲染。 |
| RI002 | blocking | fallback_render_unmarked | diagram-manifest.json#DG002 | Render Validate Agent | 安装或指定 Mermaid CLI，修复 Mermaid 源码并完成原生渲染。 |
| RI003 | blocking | fallback_render_unmarked | diagram-manifest.json#DG003 | Render Validate Agent | 安装或指定 Mermaid CLI，修复 Mermaid 源码并完成原生渲染。 |
| RI004 | blocking | fallback_render_unmarked | diagram-manifest.json#DG004 | Render Validate Agent | 安装或指定 Mermaid CLI，修复 Mermaid 源码并完成原生渲染。 |
| RI005 | blocking | fallback_render_unmarked | diagram-manifest.json#DG005 | Render Validate Agent | 安装或指定 Mermaid CLI，修复 Mermaid 源码并完成原生渲染。 |
| RI006 | blocking | fallback_render_unmarked | diagram-manifest.json#DG006 | Render Validate Agent | 安装或指定 Mermaid CLI，修复 Mermaid 源码并完成原生渲染。 |

## Next Actions

- 将阻断问题退回 Render Validate Agent 处理。
