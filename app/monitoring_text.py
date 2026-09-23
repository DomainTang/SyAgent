# -*- coding: utf-8 -*-
"""数据来源标记与监测数据文本渲染。

本工程只有三种数据来源，全部由程序打标、全程透传（MCP 返回值、Excel、图表、地图）：

| 取值 | 含义 | 来源 |
| --- | --- | --- |
| ``SAMPLE`` | 历史监测文本 | 仓库内置 ``data/测试文本*.txt`` 随机抽样 |
| ``SIMULATED`` | 本地仿真数据 | ``app/simulated_data.py`` 生成 |
| ``USER`` | 调用方直接给出的样本行 | MCP 工具入参 |

``SIMULATED`` 是硬约束标记：数据体恒定带 ``simulation_id``，文本首行恒定打印
【数据来源】及其说明，统计图加水印、烟羽地图加横幅。代码里没有"去掉仿真标记"
的开关——下游是疏散范围与现场排查决策，把仿真值当实测值用一次，代价可能是人身伤害。
"""
from __future__ import annotations

import re
from typing import Any, Optional

DATA_SOURCE_SAMPLE = "SAMPLE"
DATA_SOURCE_SIMULATED = "SIMULATED"
DATA_SOURCE_USER = "USER"

DATA_SOURCE_LABEL = {
    DATA_SOURCE_SAMPLE: "历史监测文本（SAMPLE）",
    DATA_SOURCE_SIMULATED: "本地仿真数据（SIMULATED）",
    DATA_SOURCE_USER: "用户提供（USER，未声明来源）",
}

SIMULATION_NOTICE = (
    "本批数据由本地仿真模块生成（SIMULATED），并非现场实测值；"
    "仅可用于系统联调、流程演示与故障演练，严禁用于现场处置决策。"
)
SAMPLE_NOTICE = "本批数据来自仓库内置的历史监测文本样例（SAMPLE），不是实时监测数据。"
USER_NOTICE = "本批数据由调用方直接提供（USER），来源未声明。"

_NOTICE = {
    DATA_SOURCE_SIMULATED: SIMULATION_NOTICE,
    DATA_SOURCE_SAMPLE: SAMPLE_NOTICE,
    DATA_SOURCE_USER: USER_NOTICE,
}


def data_source_label(source: Optional[str]) -> str:
    return DATA_SOURCE_LABEL.get(source or "", source or "未标注")


def notice_for(source: Optional[str]) -> str:
    return _NOTICE.get(source or "", "")


def _num(value: Any) -> Optional[float]:
    """把数值或含数值的字符串（如 “3.2 m/s”）转成 float，失败返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"[-+]?\d+(\.\d+)?", value)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def _fmt_num(value: Any) -> str:
    num = _num(value)
    if num is None:
        return str(value)
    return str(int(num)) if abs(num - round(num)) < 1e-9 else f"{num:g}"


def format_monitoring_text(monitoring: Optional[dict], data_source: str,
                           simulation_id: Optional[str] = None,
                           generated_at: Optional[str] = None,
                           scenario: Optional[str] = None) -> str:
    """把结构化监测数据渲染成固定格式文本（数据来源行是强制内容，不可省略）。"""
    lines = [f"【数据来源】{data_source_label(data_source)}"]
    notice = notice_for(data_source)
    if notice:
        lines.append(f"【数据说明】{notice}")

    head = []
    if simulation_id:
        head.append(f"批次 {simulation_id}")
    if generated_at:
        head.append(f"生成于 {generated_at}")
    if scenario:
        head.append(f"场景 {scenario}")
    if head:
        lines.append("【数据批次】" + " ｜ ".join(head))
    if not monitoring:
        return "\n".join(lines)

    lines.append("")
    lines.append("【气体浓度】")
    for r in monitoring.get("气体浓度", []):
        sensor = r.get("传感器类型") or "气体传感器"
        loc = f"，{r['位置']}" if r.get("位置") else ""
        tail = r.get("状态") or ""
        note = f"，{r['趋势说明']}" if r.get("趋势说明") else ""
        lines.append(
            f"- 点位 {r.get('点位编号', '未知')}（{sensor}{loc}）："
            f"{_fmt_num(r.get('浓度值'))} {r.get('单位', '')}，{tail}{note}"
        )
    lines.append("")

    weather = monitoring.get("气象", {})
    lines.append("【气象】")
    degree = weather.get("风向角度")
    lines.append(f"- 实时风向：{weather.get('风向', '未知')}（{_fmt_num(degree)}°）"
                 if degree is not None else f"- 实时风向：{weather.get('风向', '未知')}")
    lines.append(f"- 风速：{_fmt_num(weather.get('风速'))} m/s")
    lines.append(f"- 温湿度：{_fmt_num(weather.get('温度'))}℃ / {_fmt_num(weather.get('湿度'))}%")
    if weather.get("采样时间"):
        lines.append(f"- 采样时间：{weather['采样时间']}")
    lines.append("")

    lines.append("【设备隐患台账】")
    ledger = monitoring.get("设备隐患台账", [])
    if ledger:
        for h in ledger:
            lines.append(
                f"- 点位 {h.get('点位编号', '未知')}（{h.get('位置', '')}）：{h.get('隐患类型', '')}"
                f"（腐蚀等级 {h.get('腐蚀等级', '未评估')}），{h.get('发现时间', '')}，状态：{h.get('状态', '待处理')}"
            )
    else:
        lines.append("- 本周期内无未闭环隐患记录")
    lines.append("")

    lines.append("【厂区布置】")
    for area in monitoring.get("厂区布置", []):
        lines.append(f"- {area.get('区域名称', '')}：{area.get('类型', '')}，位于 {area.get('相对位置', '')}")
    return "\n".join(lines)


__all__ = [
    "DATA_SOURCE_SAMPLE", "DATA_SOURCE_SIMULATED", "DATA_SOURCE_USER",
    "DATA_SOURCE_LABEL", "SIMULATION_NOTICE", "SAMPLE_NOTICE", "USER_NOTICE",
    "data_source_label", "notice_for", "format_monitoring_text",
]
