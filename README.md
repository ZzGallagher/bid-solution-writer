# 高压线路无人机巡检方案自动化生成工具

本项目已精简为一条面向当前方案撰写流程的自动化链路：读取
`input/高压线路无人机巡检方案技术要求.md`，参考
`output/示例输出.docx` 的版式和章节结构，自动生成并装配：

- `1.1 建设背景`
- `2.1 功能需求`
- `2.2 性能要求`
- `2.3 非功能性要求`
- `3.1 架构设计`
- `3.2 功能设计`
- `3.3 性能设计`

`3.3` 之后的章节暂时只保留标题，后续再继续扩展生成逻辑。

## 写作流程

自动化链路固化本轮人工确认过的方案撰写流程：

1. 先读取技术要求与示例输出，识别章节结构、样式基准和每个章节的内容来源。
2. 将章节划分为两类：需求分析类章节直接摘录 Markdown 原文，设计类章节按用户给出的说明和实际任务调用外接 API 生成。
3. 每次生成正文时都围绕当前章节编号、原始需求片段、已确认输出方式和禁写内容构造提示词，不把需求简单复述成方案。
4. 生成架构设计时同时生成架构图；生成功能设计时按每个子功能点分别生成正文和一张简单流程图。
5. 所有 Mermaid 图先保存可修改的 `.mmd` 源码，再渲染 PNG 插入 Word，并在记录文件中建立章节、内容块和图表 ID 的映射。
6. 已经确认完成的章节写入目标 Word，`3.3` 之后的后续章节暂时只保留模板标题和空实现，等待下一轮流程继续补齐。

## 运行

生产模式会调用 `.env` 中配置的外接 API：

```powershell
python -m bid_solution_writer generate `
  --input input\高压线路无人机巡检方案技术要求.md `
  --template output\示例输出.docx `
  --output output\高压线路无人机巡检方案设计方案.docx
```

离线验证可使用本地草稿，不访问外接 API：

```powershell
python -m bid_solution_writer generate --allow-local-draft
```

如果 Mermaid CLI 不在 `PATH` 中，可指定渲染器：

```powershell
python -m bid_solution_writer generate --renderer-command working\tools\mmdc-npx.cmd
```

## 输出

生成记录统一写入 `output/records/`：

- `generation-map.json`：章节、来源、生成方式、图表 ID 映射。
- `content-blocks.json`：所有正文段落和章节归属。
- `diagram-specs.json`：图表标题、类型、Mermaid 源码路径和渲染状态。
- `diagrams/*.mmd`：Mermaid 源代码，供人工修改。
- `diagrams/*.png`：渲染后插入 Word 的图片。
- `run-manifest.json`：本次生成输入、输出、状态和错误信息。

## 设计约束

- 动态正文只通过 `bid_solution_writer.llm_client.call_llm_api()` 调用外接 API。
- 需求分析章节直接摘录 Markdown 原文，不改写。
- 架构图采用“分层灰底容器 + 内部横向白色模块框 + 层间单向箭头”样式。
- 软件功能流程图采用浅色、简洁、可读样式，并根据功能逻辑选择汇聚分流或纵向处理流程。
- Mermaid 渲染失败时保留 `.mmd` 并停止 Word 装配，不静默插入错误图。

## 测试

```powershell
python -m pytest
```
