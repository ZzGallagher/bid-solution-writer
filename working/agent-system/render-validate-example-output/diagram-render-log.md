# Diagram Render Log

- Run ID: `RUN-20260530-210000`
- Generated At: `2026-05-30T21:52:12+08:00`
- Renderer: `fallback-png`

## Summary

| Total | Native | Fallback | Failed | Blocked |
|---:|---:|---:|---:|---:|
| 1 | 0 | 1 | 0 | 0 |

## Diagrams

| Diagram | Status | Assembly | Image | Notes |
|---|---|---|---|---|
| DG001 | fallback_rendered | true | `working/agent-system/render-validate-example-output/diagrams/DG001.png` | render_status=fallback_rendered：原生 Mermaid 渲染不可用或失败，已生成降级 PNG，需人工关注。; 未找到 Mermaid CLI 渲染器，已生成降级 PNG。 |

## Events

| Diagram | Stage | Message |
|---|---|---|
| DG001 | source_warning | Mermaid 文件不存在，使用 diagram-specs.json 内联 mermaid 字段：working/agent-system/staging/diagrams/DG001.mmd |
| DG001 | fallback_rendered | Fallback PNG generated and explicitly marked. |
