# Diagram Plan

- Run ID: `RUN-20260601-163451`
- Generated At: `2026-06-01T16:37:54+08:00`

| Diagram | Title | Kind | Layout | Sections | Source IDs | Purpose |
|---|---|---|---|---|---|---|
| DG001 | 系统总体架构图 | architecture | flowchart TB | SEC001, SEC002, SEC003, SEC004 | T001, Q001, Q002, Q003, Q004, Q005, T002, T003, T004, Q006, T005 | 展示系统各层次及模块关系，包括数据接入、资产映射、AI分析、点云处理、业务协同。 |
| DG002 | 多源异构数据接入与解析流程图 | function_flow | flowchart TB | SEC003, SEC007, SEC008 | T001, Q001 | 描述数据接入与元数据提取流程。 |
| DG003 | 输电资产台账与空间位置映射流程图 | function_flow | flowchart TB | SEC003, SEC007, SEC008 | Q002, Q003 | 描述空间坐标匹配与部件级数据挂载流程。 |
| DG004 | 人工智能视觉分析中台流程图 | function_flow | flowchart TB | SEC003, SEC007, SEC008 | Q004, Q005, T002 | 描述可见光缺陷识别、红外热点分析及模型训练流程。 |
| DG005 | 三维点云数据处理与分析流程图 | function_flow | flowchart TB | SEC003, SEC007, SEC008 | T003, T004 | 描述点云渲染与空间距离测算流程。 |
| DG006 | 缺陷管理与业务协同流程图 | function_flow | flowchart TB | SEC003, SEC007, SEC008 | Q006, T005 | 描述人机协同复核与工单流转流程。 |
