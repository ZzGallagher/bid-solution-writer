from __future__ import annotations


CONFIRMED_WRITING_WORKFLOW = [
    "先读取技术要求与示例输出，识别章节结构、样式基准和每个章节的内容来源。",
    "将章节划分为两类：需求分析类章节直接摘录 Markdown 原文，设计类章节按用户给出的说明和实际任务调用外接 API 生成。",
    "每次生成正文时都围绕当前章节编号、原始需求片段、已确认输出方式和禁写内容构造提示词，不把需求简单复述成方案。",
    "生成架构设计时同时生成架构图；生成功能设计时按每个子功能点分别生成正文和一张简单流程图。",
    "所有 Mermaid 图先保存可修改的 .mmd 源码，再渲染 PNG 插入 Word，并在记录文件中建立章节、内容块和图表 ID 的映射。",
    "已经确认完成的章节写入目标 Word，3.3 之后的后续章节暂时只保留模板标题和空实现，等待下一轮流程继续补齐。",
]


def workflow_payload() -> dict:
    return {
        "name": "confirmed_solution_writing_workflow",
        "steps": CONFIRMED_WRITING_WORKFLOW,
        "source": "本流程来自本轮人工确认的方案撰写问答过程，应作为后续自动化写作的默认流程。",
    }
