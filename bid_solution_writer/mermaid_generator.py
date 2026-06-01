from __future__ import annotations

from pathlib import Path

from .models import DiagramSpec, FunctionPoint, ParsedRequirements


def build_architecture_diagram(parsed: ParsedRequirements, output_dir: Path) -> DiagramSpec:
    mermaid = """flowchart TB
    subgraph L1["数据采集接入层"]
        direction LR
        L1A["可见光图像接入"] ~~~ L1B["红外热像接入"] ~~~ L1C["视频流接入"] ~~~ L1D["LiDAR点云接入"] ~~~ L1E["离线导入/API推送"]
    end

    subgraph L2["数据治理与资产映射层"]
        direction LR
        L2A["元数据提取模块"] ~~~ L2B["格式清洗模块"] ~~~ L2C["空间坐标匹配模块"] ~~~ L2D["线路杆塔关联模块"] ~~~ L2E["部件级挂载模块"]
    end

    subgraph L3["智能分析服务层"]
        direction LR
        L3A["可见光缺陷识别模块"] ~~~ L3B["红外热点分析模块"] ~~~ L3C["模型训练管理模块"] ~~~ L3D["点云三维重建模块"] ~~~ L3E["空间距离测算模块"]
    end

    subgraph L4["业务协同应用层"]
        direction LR
        L4A["人机协同复核模块"] ~~~ L4B["缺陷评级管理模块"] ~~~ L4C["消缺建议生成模块"] ~~~ L4D["PMS工单流转模块"]
    end

    subgraph L5["基础支撑保障层"]
        direction LR
        L5A["统一存储模块"] ~~~ L5B["GIS服务模块"] ~~~ L5C["接口集成模块"] ~~~ L5D["权限安全模块"] ~~~ L5E["日志审计与运行监控模块"]
    end

    L1 --> L2
    L2 --> L3
    L3 --> L4
    L4 --> L5

    classDef module fill:#ffffff,stroke:#999999,stroke-width:1px,color:#111111;
    class L1A,L1B,L1C,L1D,L1E,L2A,L2B,L2C,L2D,L2E,L3A,L3B,L3C,L3D,L3E,L4A,L4B,L4C,L4D,L5A,L5B,L5C,L5D,L5E module;

    style L1 fill:#eeeeee,stroke:#999999,stroke-width:1px,color:#111111;
    style L2 fill:#eeeeee,stroke:#999999,stroke-width:1px,color:#111111;
    style L3 fill:#eeeeee,stroke:#999999,stroke-width:1px,color:#111111;
    style L4 fill:#eeeeee,stroke:#999999,stroke-width:1px,color:#111111;
    style L5 fill:#eeeeee,stroke:#999999,stroke-width:1px,color:#111111;
"""
    return DiagramSpec("DG001", "系统架构图", "architecture", "3.1", mermaid, str(output_dir / "DG001.mmd"))


def build_function_diagram(diagram_id: str, section_id: str, group_title: str, point: FunctionPoint, output_dir: Path) -> DiagramSpec:
    if is_fan_in_flow(point.title):
        mermaid = fan_in_flow(point)
    else:
        mermaid = vertical_flow(point)
    return DiagramSpec(diagram_id, f"{point.title}流程图", "function_flow", section_id, mermaid, str(output_dir / f"{diagram_id}.mmd"))


def is_fan_in_flow(title: str) -> bool:
    return any(keyword in title for keyword in ("导入", "接入", "推送", "流转"))


def fan_in_flow(point: FunctionPoint) -> str:
    if "导入" in point.title:
        sources = ("离线物理介质", "外部对象存储挂载", "标准API协议推送")
        core = "统一数据接入网关"
        decision = "数据校验与路由分发"
        outs = ("分布式图像存储池", "大容量对象存储池", "流媒体存储集群")
        labels = ("可见光/红外影像", "LiDAR点云文件", "无人机视频流")
    else:
        sources = ("前置业务数据", "人工审核结果", "外部系统接口")
        core = f"{point.title}处理模块"
        decision = "规则校验与业务分发"
        outs = ("业务结果库", "过程记录库", "异常待办池")
        labels = ("有效结果", "过程记录", "异常数据")
    return f"""flowchart TB
    A["{sources[0]}"]
    B["{sources[1]}"]
    C["{sources[2]}"]

    D["{core}"]
    E{{"{decision}"}}

    F[("{outs[0]}")]
    G[("{outs[1]}")]
    H[("{outs[2]}")]

    A --> D
    B --> D
    C --> D
    D --> E
    E -->|{labels[0]}| F
    E -->|{labels[1]}| G
    E -->|{labels[2]}| H

    classDef source fill:#f5f5f5,stroke:#999999,color:#111111,stroke-width:1px;
    classDef process fill:#f2f2f2,stroke:#999999,color:#111111,stroke-width:1px;
    classDef decision fill:#f2f2f2,stroke:#999999,color:#111111,stroke-width:1px;
    classDef storage fill:#f5f5f5,stroke:#999999,color:#111111,stroke-width:1px;

    class A,B,C source;
    class D process;
    class E decision;
    class F,G,H storage;
"""


def vertical_flow(point: FunctionPoint) -> str:
    input_label = {
        "元数据提取与标准化": "已导入巡检影像与遥测数据",
        "空间坐标匹配": "待匹配影像空间元数据",
        "部件级数据挂载": "已匹配至特定杆塔的影像数据",
        "可见光缺陷识别": "可见光巡检影像",
        "红外热点分析": "红外热像与温度矩阵",
        "模型训练与管理": "人工复核修正样本",
        "通道三维重建": "LiDAR点云与航迹数据",
        "空间距离测算": "导线/地面/植被点云模型",
        "人机协同复核": "AI初筛缺陷记录",
    }.get(point.title, f"{point.title}输入数据")
    store_label = {
        "部件级数据挂载": "部件级数字资产库",
        "空间坐标匹配": "线路杆塔空间索引库",
        "元数据提取与标准化": "标准元数据资源库",
        "可见光缺陷识别": "可见光缺陷结果库",
        "红外热点分析": "红外热缺陷结果库",
        "模型训练与管理": "模型版本与样本库",
        "通道三维重建": "三维通道模型库",
        "空间距离测算": "空间隐患测算结果库",
        "人机协同复核": "确认缺陷台账库",
    }.get(point.title, f"{point.title}结果库")
    return f"""flowchart TB
    A["{input_label}"]
    B("{point.title}数据加载")
    C("规则解析与标准化处理")
    D("业务目标识别与结果计算")
    E{{"一致性校验与人工复核"}}
    F["生成{point.title}结果"]
    G[("{store_label}")]

    A --> B
    B --> C
    C --> D
    D --> E
    E --> F
    F --> G

    classDef source fill:#f5f5f5,stroke:#999999,color:#111111,stroke-width:1px;
    classDef process fill:#f2f2f2,stroke:#999999,color:#111111,stroke-width:1px;
    classDef decision fill:#f2f2f2,stroke:#999999,color:#111111,stroke-width:1px;
    classDef storage fill:#f5f5f5,stroke:#999999,color:#111111,stroke-width:1px;

    class A,F source;
    class B,C,D process;
    class E decision;
    class G storage;
"""


def write_mermaid_files(diagrams: list[DiagramSpec]) -> list[DiagramSpec]:
    for diagram in diagrams:
        path = Path(diagram.mermaid_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(diagram.mermaid.rstrip() + "\n", encoding="utf-8")
    return diagrams
