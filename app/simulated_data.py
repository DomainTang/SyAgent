# -*- coding: utf-8 -*-
"""厂区多源监测数据 —— 本地仿真生成器（SIMULATED）。

用途：在没有对接 DCS/GDS/OPC-UA 的情况下，为联调、演示、故障演练提供
**格式与单位正确、量级合理、点位与设备编号一致**的监测数据。

三条不可突破的约束：
1. 生成的数据恒定携带 ``data_source = "SIMULATED"`` 与 ``simulation_id``；
2. 文本渲染时恒定输出【数据来源】行（见 ``app.monitoring_text``）；
3. 不存在任何"去掉仿真标记"的开关。

仿真值不是实测值，任何现场处置决策都必须以现场仪表/DCS 读数为准。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Optional

from .monitoring_text import DATA_SOURCE_SIMULATED, SIMULATION_NOTICE

# ============================================================
# 1. 厂区基础台账（点位名称取自现场监测点命名习惯）
# ============================================================
PLANT_AREAS: list[dict[str, str]] = [
    {"区域名称": "生产指挥中心", "类型": "装置区", "相对位置": "厂区中部偏南"},
    {"区域名称": "18罐区东南角", "类型": "罐区", "相对位置": "厂区西北侧"},
    {"区域名称": "19罐区东南角", "类型": "罐区", "相对位置": "厂区西北侧，紧邻18罐区"},
    {"区域名称": "21罐区西南角", "类型": "罐区", "相对位置": "厂区西侧"},
    {"区域名称": "8#~12#罐区-8罐区南", "类型": "罐区", "相对位置": "厂区西北侧偏南"},
    {"区域名称": "汽油吸附脱硫装置", "类型": "装置区", "相对位置": "厂区中部"},
    {"区域名称": "硫磺装置", "类型": "装置区", "相对位置": "厂区东北侧"},
    {"区域名称": "煤气化框架", "类型": "装置区", "相对位置": "厂区东侧"},
    {"区域名称": "污水处理场生化池", "类型": "装置区", "相对位置": "厂区西南侧"},
    {"区域名称": "催化分馏框架三层平台", "类型": "装置区", "相对位置": "厂区中部偏北"},
    {"区域名称": "腈纶区域-Ⅱ丙V-4011框架平台", "类型": "装置区", "相对位置": "厂区东南侧"},
    {"区域名称": "481火炬设施", "类型": "火炬", "相对位置": "厂区东南角"},
    {"区域名称": "柴油装车平台", "类型": "装置区", "相对位置": "厂区南侧，靠近厂前区"},
    {"区域名称": "蜡油加氢压缩机PSA平台", "类型": "装置区", "相对位置": "厂区北侧"},
    {"区域名称": "中心控制室", "类型": "装置区", "相对位置": "厂区中部"},
    {"区域名称": "会议中心", "类型": "敏感区域", "相对位置": "厂区南侧厂前区"},
    {"区域名称": "腈纶办公楼", "类型": "敏感区域", "相对位置": "厂区东南侧"},
    {"区域名称": "科技大楼", "类型": "敏感区域", "相对位置": "厂区西南侧"},
    {"区域名称": "化肥南围墙", "类型": "敏感区域", "相对位置": "厂区北侧厂界"},
]

# 传感器类型 → 气体、合法单位、正常量级、报警量级。
# normal/alert 区间按常见仪表量程与报警阈值设定，单位严格落在 GAS_UNITS 白名单内。
SENSOR_TYPES: list[dict[str, Any]] = [
    {"传感器类型": "可燃气体传感器", "气体种类": "可燃气体", "单位": "%LEL",
     "normal": (0.0, 3.0), "alert": (25.0, 62.0), "decimals": 0},
    {"传感器类型": "甲烷传感器", "气体种类": "甲烷", "单位": "%LEL",
     "normal": (0.0, 2.0), "alert": (8.0, 30.0), "decimals": 1},
    {"传感器类型": "甲烷传感器", "气体种类": "甲烷", "单位": "ppm",
     "normal": (0.0, 60.0), "alert": (400.0, 1800.0), "decimals": 0},
    {"传感器类型": "硫化氢传感器", "气体种类": "硫化氢", "单位": "ppm",
     "normal": (0.0, 1.0), "alert": (8.0, 45.0), "decimals": 1},
    {"传感器类型": "VOC传感器", "气体种类": "VOC", "单位": "ppm",
     "normal": (0.05, 0.8), "alert": (2.5, 18.0), "decimals": 2},
    {"传感器类型": "VOC传感器", "气体种类": "VOC", "单位": "mg/m3",
     "normal": (0.2, 2.0), "alert": (6.0, 40.0), "decimals": 2},
    {"传感器类型": "一氧化碳传感器", "气体种类": "一氧化碳", "单位": "ppm",
     "normal": (0.0, 2.0), "alert": (20.0, 90.0), "decimals": 0},
]

WIND_DIRECTIONS: list[tuple[str, int]] = [
    ("北风", 0), ("东北风", 45), ("东风", 90), ("东南风", 135),
    ("南风", 180), ("西南风", 225), ("西风", 270), ("西北风", 315),
]

HAZARD_TYPES = ["法兰密封面腐蚀", "阀门填料老化", "管道焊缝砂眼", "金属软管龟裂",
                "呼吸阀失效", "管托腐蚀减薄", "机泵机械密封渗漏"]
EQUIPMENT_PREFIX = ["V", "E", "P", "F", "TK", "R"]
CORROSION_GRADES = ["A", "B", "C"]

SCENARIOS = ["normal", "leak", "leak_multi", "sensor_fault"]
_SCENARIO_WEIGHTS = [0.15, 0.50, 0.20, 0.15]
SCENARIO_LABEL = {
    "normal": "常规工况（无异常点位）",
    "leak": "单点异常（疑似泄漏）",
    "leak_multi": "多点异常（疑似区域泄漏）",
    "sensor_fault": "仪表异常（单点数据可疑）",
}

# ============================================================
# 2. 内部工具
# ============================================================
def _round(rng: random.Random, value: float, decimals: int) -> float:
    return round(value, decimals) if decimals else float(round(value))


def _pick_area(rng: random.Random, exclude: Optional[set] = None) -> dict:
    pool = [a for a in PLANT_AREAS if not exclude or a["区域名称"] not in exclude]
    return dict(rng.choice(pool or PLANT_AREAS))


def _hazard_for(rng: random.Random, area: dict, now: datetime, serious: bool) -> dict:
    """为指定区域生成一条隐患台账记录（泄漏场景下与异常点位联动）。"""
    days_ago = rng.randint(20, 180)
    return {
        "点位编号": area.get("点位编号") or f"{rng.randint(1, 260):03d}",
        "位置": area["区域名称"],
        "设备编号": f"{rng.choice(EQUIPMENT_PREFIX)}-{rng.randint(101, 4210)}{rng.choice('ABC')}",
        "隐患类型": rng.choice(HAZARD_TYPES),
        "发现时间": (now - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
        "腐蚀等级": rng.choice(["B", "C"]) if serious else rng.choice(CORROSION_GRADES),
        "状态": rng.choice(["待处理", "已立项", "维修中"]) if serious else rng.choice(["已闭环", "已闭环", "待处理"]),
    }


def _wind_speed_level(speed_ms: float) -> str:
    """m/s → 蒲福风力等级（兼容语料文本里的“风速为 N 级”写法）。"""
    table = [(0.2, "0"), (1.5, "1"), (3.3, "2"), (5.4, "3-4"), (7.9, "5"),
             (10.7, "6"), (13.8, "7"), (17.1, "8")]
    for upper, label in table:
        if speed_ms <= upper:
            return label
    return "8+"


def wind_speed_level(speed_ms) -> str:
    """公开版本：把 m/s 转成风力等级，供 CLI / HTTP 接口 / MCP 复用。"""
    try:
        return _wind_speed_level(float(speed_ms))
    except (TypeError, ValueError):
        return "2"

# ============================================================
# 3. 核心生成器
# ============================================================
def generate_monitoring_data(
    seed: Optional[int] = None,
    scenario: Optional[str] = None,
    now: Optional[datetime] = None,
    point_count: int = 6,
) -> dict:
    """生成一批与现场格式、单位一致的监测数据（恒定带 SIMULATED 标记）。"""
    rng = random.Random(seed)
    now = now or datetime.now()
    if scenario in (None, "", "auto"):
        scenario = rng.choices(SCENARIOS, weights=_SCENARIO_WEIGHTS, k=1)[0]
    if scenario not in SCENARIOS:
        raise ValueError(f"未知仿真场景: {scenario}，可选 {SCENARIOS}")

    point_count = max(3, min(int(point_count), 12))
    used_areas = set()
    readings: list[dict] = []

    alert_count = {"normal": 0, "leak": 1, "leak_multi": 2, "sensor_fault": 1}[scenario]
    alert_indices = set(range(alert_count))

    # 采样时刻一律早于当前时间，保证不会出现"未来时间戳"
    base_ts = (now - timedelta(seconds=rng.randint(1, 60))).replace(microsecond=0)

    for i in range(point_count):
        spec = rng.choice(SENSOR_TYPES)
        area = _pick_area(rng, exclude=used_areas)
        used_areas.add(area["区域名称"])
        point_id = f"{rng.randint(1, 260):03d}"
        ts = base_ts - timedelta(seconds=rng.randint(0, 240))
        is_alert = i in alert_indices

        if is_alert and scenario == "sensor_fault":
            value = _round(rng, rng.uniform(0, spec["alert"][1] * 2), spec["decimals"])
            status = "离线" if rng.random() < 0.4 else "波动"
            trend = "读数在 3 个采样周期内大幅跳变，仪表自检异常"
        elif is_alert:
            lo, hi = spec["alert"]
            value = _round(rng, rng.uniform(lo, hi), spec["decimals"])
            status = "报警" if value >= lo * 1.2 else "上升"
            rise_at = (ts - timedelta(minutes=rng.randint(3, 25))).strftime("%H:%M")
            trend = f"{rise_at} 起持续上升" if status == "报警" else f"{rise_at} 起缓慢上升"
        else:
            lo, hi = spec["normal"]
            value = _round(rng, rng.uniform(lo, hi), spec["decimals"])
            if scenario in ("leak", "leak_multi") and i <= alert_count and rng.random() < 0.5:
                value = _round(rng, rng.uniform(hi, hi * 3 + 0.5), spec["decimals"])
                status, trend = "波动", "较基线抬升，尚未达到报警值"
            else:
                status, trend = "正常", ""

        readings.append({
            "点位编号": point_id,
            "传感器类型": spec["传感器类型"],
            "气体种类": spec["气体种类"],
            "位置": area["区域名称"],
            "浓度值": value,
            "单位": spec["单位"],
            "时间戳": ts.strftime("%H:%M:%S"),
            "状态": status,
            "趋势说明": trend,
        })

    # ---- 气象 ----：泄漏场景下风向偏向敏感区域，便于下游做风险研判
    if scenario in ("leak", "leak_multi") and rng.random() < 0.6:
        direction, degree = "东北风", 45
    else:
        direction, degree = rng.choice(WIND_DIRECTIONS)
    weather = {
        "风向": direction,
        "风向角度": degree,
        "风速": round(rng.uniform(0.8, 6.5), 1),
        "温度": round(rng.uniform(8.0, 34.0), 1),
        "湿度": float(round(rng.uniform(35.0, 88.0))),
        "采样时间": base_ts.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # ---- 隐患台账 ----
    ledger: list[dict] = []
    if scenario in ("leak", "leak_multi"):
        for r in readings:
            if r["状态"] in ("报警", "上升"):
                ledger.append(_hazard_for(rng, {"区域名称": r["位置"], "点位编号": r["点位编号"]}, now, True))
        ledger.append(_hazard_for(rng, _pick_area(rng, used_areas), now, False))
    else:
        ledger.append(_hazard_for(rng, _pick_area(rng, used_areas), now, False))

    # ---- 厂区布置：异常点位周边 + 至少两个敏感区域 ----
    layout: list[dict] = []
    for r in readings:
        hit = next((a for a in PLANT_AREAS if a["区域名称"] == r["位置"]), None)
        if hit and hit not in layout:
            layout.append(dict(hit))
    sensitive = [a for a in PLANT_AREAS if a["类型"] == "敏感区域"]
    for a in rng.sample(sensitive, k=min(2, len(sensitive))):
        if a not in layout:
            layout.append(dict(a))

    simulation_id = f"SIM-{now.strftime('%Y%m%d-%H%M%S')}-{rng.randrange(0x10000):04X}"
    return {
        "data_source": DATA_SOURCE_SIMULATED,
        "simulation_id": simulation_id,
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "scenario": scenario,
        "scenario_label": SCENARIO_LABEL[scenario],
        "seed": seed,
        "notice": SIMULATION_NOTICE,
        "气体浓度": readings,
        "气象": weather,
        "设备隐患台账": ledger,
        "厂区布置": layout,
    }


def build_sample_lines(data: dict, count: Optional[int] = None,
                       rng: Optional[random.Random] = None) -> list:
    """把仿真读数转成语料库同构的样本行，供既有预测链路直接消费。

    形如：``传感器030，位置为生产指挥中心，风速为2级，风向为东北风``
    """
    rng = rng or random.Random(data.get("seed"))
    weather = data.get("气象", {})
    wind_speed = _wind_speed_level(float(weather.get("风速", 3.0)))
    wind_dir = weather.get("风向", "东风")

    lines = [
        f"传感器{r['点位编号']}，位置为{r['位置']}，风速为{wind_speed}级，风向为{wind_dir}"
        for r in data.get("气体浓度", [])
    ]
    if count is not None and count > 0 and len(lines) > count:
        lines = rng.sample(lines, count)
    return lines


__all__ = [
    "PLANT_AREAS", "SENSOR_TYPES", "SCENARIOS", "SCENARIO_LABEL",
    "generate_monitoring_data", "build_sample_lines", "wind_speed_level",
]
