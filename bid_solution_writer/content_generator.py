from __future__ import annotations

from typing import Any

from .llm_client import call_llm_api
from .models import FunctionPoint, ParsedRequirements
from .prompt_templates import architecture_payload, background_payload, function_design_payload, performance_payload


class ContentGenerator:
    def __init__(self, allow_local_draft: bool = False) -> None:
        self.allow_local_draft = allow_local_draft

    def background(self, parsed: ParsedRequirements) -> list[str]:
        if self.allow_local_draft:
            return local_background(parsed)
        return normalize_paragraphs(call_llm_api(background_payload(parsed)))

    def architecture(self, parsed: ParsedRequirements) -> list[str]:
        if self.allow_local_draft:
            return local_architecture(parsed)
        return normalize_paragraphs(call_llm_api(architecture_payload(parsed)))

    def function_design(self, group_title: str, point: FunctionPoint, section_id: str) -> list[str]:
        if self.allow_local_draft:
            return local_function_design(group_title, point)
        return normalize_paragraphs(call_llm_api(function_design_payload(group_title, point, section_id)))

    def performance(self, parsed: ParsedRequirements) -> list[str]:
        if self.allow_local_draft:
            return local_performance(parsed.performance_items)
        return normalize_titled_paragraphs(call_llm_api(performance_payload(parsed.performance_items)))


def normalize_paragraphs(response: Any) -> list[str]:
    if isinstance(response, dict):
        content = response.get("content")
    else:
        content = response
    if isinstance(content, list):
        values = []
        for item in content:
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("content") or "").strip()
            else:
                text = str(item).strip()
            if text:
                values.append(text)
        return values
    if isinstance(content, str):
        return [item.strip() for item in content.split("\n\n") if item.strip()]
    raise ValueError("LLM 返回格式不符合正文生成契约。")


def normalize_titled_paragraphs(response: Any) -> list[str]:
    if isinstance(response, dict):
        content = response.get("content")
    else:
        content = response
    if not isinstance(content, list):
        return normalize_paragraphs(response)
    values = []
    for item in content:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
            text = str(item.get("text") or item.get("content") or "").strip()
            if title:
                values.append(title)
            if text:
                values.append(text)
        elif str(item).strip():
            values.append(str(item).strip())
    return values


def local_background(parsed: ParsedRequirements) -> list[str]:
    return [
        f"本项目的建设背景源于高压输电线路运维管理模式正由传统人工巡检向无人机巡检、智能识别和业务闭环协同转型。{parsed.project_overview} 该类场景下，线路通道跨度大、环境复杂、人工巡线安全风险高且数据沉淀不足，亟需通过平台化方式提升巡检效率、隐患发现能力和结果追溯水平。",
        "随着可见光影像、红外热像、视频流和LiDAR点云等巡检数据规模持续扩大，单纯依赖人工阅片和离线文件管理已难以满足高压线路精细化运维要求。系统建设需要将多源异构数据统一接入、解析、标准化和资产绑定，形成面向线路、杆塔、部件和通道环境的数字化巡检底座。",
        "通过引入深度学习视觉分析、红外热点研判、三维点云空间测算和PMS工单协同能力，平台可推动隐患识别从人工经验判断转向数据驱动研判，从单次巡检成果转向缺陷评级、整改派发、复核归档的闭环管理，为输电线路状态检修和精益运维提供支撑。",
    ]


def local_architecture(parsed: ParsedRequirements) -> list[str]:
    return [
        "本高压线路无人机巡检软件架构围绕多源数据接入、资产空间映射、智能分析研判、缺陷闭环处置和平台运行保障五类能力展开，形成由数据采集接入层、数据治理与资产映射层、智能分析服务层、业务协同应用层、基础支撑保障层组成的分层体系。整体架构面向可见光图像、红外热像图、视频流和LiDAR点云等巡检成果，建立统一接入、统一解析、统一归档和统一调用机制，使原始巡检文件能够转化为可检索、可定位、可分析和可流转的业务数据。",
        "数据采集接入层负责接收离线物理介质批量导入、对象存储挂载和标准API接口推送等多种来源的数据。该层需要对文件类型、任务批次、数据来源、完整性和重复性进行基础校验，并根据数据类型建立影像、视频和点云的初始归档关系。通过将接入方式抽象为统一数据入口，平台可以兼容现场离线作业、中心化存储管理和系统间在线集成等应用场景。",
        "数据治理与资产映射层负责完成元数据提取、格式清洗、坐标统一和资产台账绑定。系统应自动读取影像EXIF信息和飞行平台遥测数据，结合电网GIS系统中的线路路径、杆塔坐标和通道范围，通过空间运算判别影像所属线路段、杆塔和部件节点。该层是巡检数据进入输电运维语境的关键，使后续缺陷识别结果能够准确落到具体线路资产上。",
        "智能分析服务层由人工智能视觉分析中台和三维点云空间测算引擎构成。可见光缺陷识别模块面向断股、绝缘子破损、开口销脱落、金具锈蚀、鸟巢和外力破坏等典型隐患开展自动检测；红外热点分析模块解析温度矩阵并结合同类设备温差与历史趋势进行异常致热研判；模型训练与管理模块支撑人工复核样本回流和模型版本管理；点云处理模块负责通道三维重建、导线和植被提取、交跨距离及树障安全距离测算。",
        "业务协同应用层将算法结果转化为运维人员可直接处理的缺陷记录和隐患任务。系统应提供人机协同复核界面，支持缺陷确认、类别修正、等级评定和处置建议生成，并通过接口与PMS等生产管理系统衔接，触发工单流转、整改跟踪和复检归档。基础支撑保障层则提供统一存储、GIS服务、接口集成、权限安全、日志审计和运行监控能力，保障平台在大批量数据导入、算法任务集中执行和多用户并发查询场景下稳定运行。",
    ]


def local_function_design(group_title: str, point: FunctionPoint) -> list[str]:
    return [
        f"{point.title}功能面向{group_title}中的核心业务环节，重点解决“{point.body}”所描述的能力落地问题。系统应将该功能纳入统一巡检任务和数据资源管理体系，明确输入数据、处理规则、输出结果和异常反馈方式，使其既能独立支撑具体业务操作，也能与上下游的数据接入、资产映射、智能分析和工单协同流程衔接。",
        "在实现过程中，平台应围绕标准化处理、状态跟踪和结果可追溯建立功能逻辑。系统需要对输入信息进行必要校验，对处理过程形成可查询状态，对处理失败、低置信度或需人工确认的情况提供明确提示，并将最终结果写入对应的业务资源库或任务记录中，避免巡检成果停留在零散文件或临时结果层面。",
        f"该功能的设计重点在于提升{point.title}的自动化、规范化和业务可用性。通过将处理结果与线路、杆塔、部件、缺陷或工单等业务对象关联，平台能够支撑后续查询统计、人工复核、趋势分析和闭环处置，为高压线路无人机巡检业务的规模化运行提供基础能力。",
    ]


def local_performance(items: list[str]) -> list[str]:
    result = []
    for item in items:
        title = item.split("：", 1)[0].split(":", 1)[0].strip("；; ")
        result.append(f"{title}设计")
        result.append(f"针对“{item}”的要求，系统应在前端交互、服务接口、数据查询、缓存策略和后台任务调度等方面进行协同优化。平台应控制同步操作的数据返回范围，采用分页加载、异步处理、索引优化、状态反馈和资源监控等机制，保证用户在巡检数据查阅、统计分析、任务处理和业务协同时获得稳定、明确、可追踪的响应体验。")
    return result
