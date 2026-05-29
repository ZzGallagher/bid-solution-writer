# 投标方案生成工具

本项目用于根据 `input/` 中的招标资料和 `templates/` 中的 Word 模板生成投标方案初稿。

## 技术路线

当前主流程入口为 `working/generate_bid_solution.py`：

1. 读取 `input/技术要求.docx`、`input/商务要求.docx`、`input/技术评分表.docx`。
2. 抽取项目名称、技术标准、功能要求、性能要求、质量要求、商务条款、评分项和交付物表。
3. 将抽取结果写入 `output/records/requirements.json`。
4. 生成 `output/records/requirements-matrix.md`，建立“来源条款 -> 方案章节”的映射。
5. 扫描模板中的 `COPY`、`GEN`、`REVIEW`、`CONFIRM` 占位符。
6. 根据结构化需求生成段落、表格、图片等内容块。
7. 复制 `templates/投标方案模板.docx` 到 `output/`，并使用 `python-docx` 将内容块填入 Word。
8. 输出占位符日志、人工确认清单、复核清单和覆盖检查报告。

## 数据流

```text
input/*.docx
  -> Word 文本和表格抽取
  -> requirements.json
  -> requirements-matrix.md
  -> placeholder blocks
  -> output/*.docx
  -> records/*.md
```

## 占位符处理规则

- `COPY`：从 input 原文摘录或整理，不扩写事实。
- `GEN`：根据结构化需求、评分项和模板上下文生成方案正文。
- `REVIEW`：可以生成初稿，但进入复核清单。
- `CONFIRM`：保留在 Word 中，进入人工确认清单，不自动编造。

## 运行

```powershell
python working\generate_bid_solution.py
```

依赖见 `requirements.txt`。
