from __future__ import annotations

from .models import FunctionPoint, ParsedRequirements
from .workflow import workflow_payload


def background_payload(parsed: ParsedRequirements) -> dict:
    return {
        "task": "generate_section",
        "section": "1.1 建设背景",
        "writing_workflow": workflow_payload(),
        "source": {"项目概述": parsed.project_overview},
        "rules": [
            "基于项目概述分析建设背景。",
            "采用正式投标/建设方案语气。",
            "输出大段自然段文字，不要列表。",
            "重点覆盖传统人工巡线痛点、多源数据条件、智能化技术基础、PMS业务闭环和数字化转型价值。",
            "不要编造人员、资质、业绩、报价和服务承诺。",
        ],
        "output_contract": {"content": ["段落1", "段落2"]},
    }


def architecture_payload(parsed: ParsedRequirements) -> dict:
    return {
        "task": "generate_section",
        "section": "3.1 架构设计",
        "writing_workflow": workflow_payload(),
        "source": {"功能要求": parsed.function_requirements.body},
        "rules": [
            "根据功能要求设计软件总体架构。",
            "架构描述采用大段文字，文字数量不少于2000字。",
            "围绕数据采集接入层、数据治理与资产映射层、智能分析服务层、业务协同应用层、基础支撑保障层展开。",
            "体现可见光、红外、视频、LiDAR点云、GIS、资产台账、AI视觉分析、MLOps、点云测算、缺陷复核、PMS工单闭环。",
            "不要编造人员、资质、业绩、报价和服务承诺。",
        ],
        "output_contract": {"content": ["段落1", "段落2"]},
    }


def function_design_payload(group_title: str, point: FunctionPoint, section_id: str) -> dict:
    return {
        "task": "generate_section",
        "section": section_id,
        "writing_workflow": workflow_payload(),
        "source": {"一级功能": group_title, "子功能点": point.title, "功能描述": point.body},
        "rules": [
            "完成该软件子功能点的功能设计。",
            "采用2到3段的大段纯文字描述。",
            "围绕业务定位、输入数据、处理机制、输出结果、校验/异常和业务价值展开。",
            "不要写成简单复述需求。",
            "不要编造人员、资质、业绩、报价和服务承诺。",
        ],
        "output_contract": {"content": ["段落1", "段落2"]},
    }


def performance_payload(items: list[str]) -> dict:
    return {
        "task": "generate_section",
        "section": "3.3 性能设计",
        "writing_workflow": workflow_payload(),
        "source": {"性能要求": items},
        "rules": [
            "根据性能要求完成系统性能设计。",
            "每条性能要求输出1段大段文字。",
            "每段话前设置1个概括性标题。",
            "标题和正文需成对返回。",
            "保持正式方案语气。",
        ],
        "output_contract": {"content": [{"title": "标题", "text": "段落正文"}]},
    }
