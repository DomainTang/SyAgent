# -*- coding: utf-8 -*-
"""厂区多源监测数据：统一结构、程序化校验、固定文本渲染、取数入口。

职责边界
--------
1. 本模块**不产生任何数据**：仿真数据一律由 ``app/simulated_data.py`` 生成，
   现场数据由 DCS/GDS/OPC-UA 等数据源通过 provider / HTTP 端点注入。
2. "数据是否齐备"由程序判定（:func:`validate_monitoring_data`），不交给大模型
   自由心证，避免同一个模型一会儿编数据、一会儿又说没数据。
3. 每批数据都带 ``data_source`` 标记，取值为 REAL / SIMULATED / UNAVAILABLE。
   该标记是强制字段：MCP 工具返回、文本渲染、Excel、图表、地图都必须原样透传。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from datetime import datetime
from typing import Any, Callable, Optional

# ============================================================
# 1. 常量与数据来源标记
# ============================================================
DATA_SOURCE_REAL = "REAL"
DATA_SOURCE_SIMULATED = "SIMULATED"
DATA_SOURCE_UNAVAILABLE = "UNAVAILABLE"

DATA_SOURCE_LABEL = {
    DATA_SOURCE_REAL: "现场实测数据（REAL）",
    DATA_SOURCE_SIMULATED: "本地仿真数据（SIMULATED）",
    DATA_SOURCE_UNAVAILABLE: "未获取到数据（UNAVAILABLE）",
}

SIMULATION_NOTICE = (
    "本批数据由本地仿真模块生成（SIMULATED），并非现场实测值；"
    "仅可用于系统联调、流程演示与故障演练，严禁用于现场处置决策。"
)
REAL_NOTICE = "本批数据来自现场监测数据源（REAL）。"
UNAVAILABLE_NOTICE = (
    "未配置或未获取到现场监测数据源，无法开展有效的泄漏溯源分析。"
)

# 运行模式：auto / real / simulate / off
MODE_ENV = "SHIHUA_DATA_MODE"
ENDPOINT_ENV = "SHIHUA_REALTIME_ENDPOINT"
# 公开演示服务默认走仿真（输出恒定带 SIMULATED 标记）；对接现场时置为 real 或 off。
DEFAULT_MODE = "simulate"

# 气体类型 → 合法浓度单位。单位不合法直接判为阻塞，
# 防止 ppm / %LEL / %VOL 混用这类会让现场误判的低级错误。
GAS_UNITS: dict[str, set[str]] = {
    "可燃气体": {"%LEL"},
    "甲烷": {"ppm", "%LEL", "%VOL"},
    "硫化氢": {"ppm", "mg/m3"},
    "VOC": {"ppm", "mg/m3", "ppb"},
    "一氧化碳": {"ppm", "mg/m3"},
    "氧气": {"%VOL"},
}

READING_STATUS = {"正常", "波动", "上升", "报警", "离线"}

_TIMESTAMP_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$|^\d{4}-\d{2}-\d{2}([ T]\d{1,2}:\d{2}(:\d{2})?)?$")


# ============================================================
# 2. 程序化校验
# ============================================================
def _num(value: Any) -> Optional[float]:
    """把数值或含数值的字符串（如 “3.2 m/s”）转成 float，失败返回 None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"[-+]?\d+(\.\d+)?", value)
        if m:
            try:
                return float(m.group(0))
            except ValueError:
                return None
    return None


def _check_readings(readings: Any) -> tuple[bool, list[str], list[str]]:
    """校验气体读数块，返回 (是否通过, 阻塞原因, 提示)。"""
    blocking: list[str] = []
    warnings: list[str] = []

    if not isinstance(readings, list) or not readings:
        return False, ["气体传感器实时读数缺失（点位+浓度+时间戳）"], warnings

    for i, item in enumerate(readings, 1):
        tag = f"第{i}条读数"
        if not isinstance(item, dict):
            blocking.append(f"{tag}结构非法，应为对象")
            continue
        point = item.get("点位编号") or item.get("point_id")
        if not point:
            blocking.append(f"{tag}缺少点位编号")
        gas = item.get("气体种类") or item.get("gas")
        unit = (item.get("单位") or item.get("unit") or "").strip()
        value = _num(item.get("浓度值", item.get("value")))
        if value is None:
            blocking.append(f"{tag}缺少可解析的浓度值")
        elif value < 0:
            blocking.append(f"{tag}浓度值为负（{value}），数据不可信")

        if not gas:
            blocking.append(f"{tag}缺少气体种类")
        elif unit:
            allowed = GAS_UNITS.get(str(gas))
            if allowed and unit not in allowed:
                blocking.append(
                    f"{tag}单位不匹配：{gas} 的合法单位是 {'/'.join(sorted(allowed))}，实际为 {unit}"
                )
        else:
            blocking.append(f"{tag}缺少单位")

        ts = item.get("时间戳") or item.get("timestamp")
        if not ts:
            blocking.append(f"{tag}缺少时间戳")
        elif isinstance(ts, str) and not _TIMESTAMP_RE.match(ts.strip()):
            warnings.append(f"{tag}时间戳格式非标准（{ts}）")

        status = item.get("状态") or item.get("status")
        if status and status not in READING_STATUS:
            warnings.append(f"{tag}状态取值非常规：{status}")

    return (not blocking), blocking, warnings


def _check_weather(weather: Any) -> tuple[bool, list[str], list[str]]:
    blocking: list[str] = []
    warnings: list[str] = []

    if not isinstance(weather, dict) or not weather:
        return False, ["气象参数缺失（风向、风速、温湿度）"], warnings

    direction = weather.get("风向") or weather.get("wind_direction")
    degree = _num(weather.get("风向角度") or weather.get("wind_direction_deg"))
    speed = _num(weather.get("风速") if "风速" in weather else weather.get("风速(m/s)"))
    if speed is None:
        speed = _num(weather.get("wind_speed_ms"))
    temp = _num(weather.get("温度") if "温度" in weather else weather.get("温度(℃)"))
    if temp is None:
        temp = _num(weather.get("temperature_c"))
    humidity = _num(weather.get("湿度") if "湿度" in weather else weather.get("湿度(%)"))
    if humidity is None:
        humidity = _num(weather.get("humidity_pct"))

    if not direction and degree is None:
        blocking.append("缺少风向（方位或角度）")
    if degree is not None and not (0 <= degree <= 360):
        blocking.append(f"风向角度超出 0~360 范围：{degree}")
    if speed is None:
        blocking.append("缺少风速")
    elif not (0 <= speed <= 60):
        blocking.append(f"风速超出合理范围 0~60 m/s：{speed}")
    if temp is None:
        warnings.append("缺少温度，扩散参数将使用默认值")
    elif not (-50 <= temp <= 60):
        warnings.append(f"温度超出合理范围：{temp}")
    if humidity is None:
        warnings.append("缺少湿度，扩散参数将使用默认值")
    elif not (0 <= humidity <= 100):
        warnings.append(f"湿度超出合理范围：{humidity}")

    return (not blocking), blocking, warnings


def _check_ledger(ledger: Any) -> tuple[bool, list[str]]:
    if ledger is None:
        return False, ["设备隐患/泄漏台账未提供（根因推断将降级）"]
    if not isinstance(ledger, list):
        return False, ["设备隐患台账结构非法，应为列表"]
    for item in ledger:
        if isinstance(item, dict) and not (item.get("点位编号") or item.get("设备编号") or item.get("location")):
            return False, ["隐患台账条目缺少点位/设备编号"]
    return True, []


def _check_layout(layout: Any) -> tuple[bool, list[str]]:
    if layout is None:
        return False, ["厂区平面布置未提供（定位精度将降级至区域级）"]
    if not isinstance(layout, list) or not layout:
        return False, ["厂区布置为空，应为区域列表"]
    return True, []


def validate_monitoring_data(monitoring: Optional[dict], data_source: Optional[str] = None) -> dict[str, Any]:
    """对一批监测数据做程序化体检，返回可直接展示 / 喂给模型的结构化结论。"""
    monitoring = monitoring or {}

    ok_gas, block_gas, warn_gas = _check_readings(monitoring.get("气体浓度") or monitoring.get("gas_readings"))
    ok_weather, block_weather, warn_weather = _check_weather(monitoring.get("气象") or monitoring.get("weather"))
    ok_ledger, block_ledger = _check_ledger(monitoring.get("设备隐患台账") or monitoring.get("hazard_ledger"))
    ok_layout, block_layout = _check_layout(monitoring.get("厂区布置") or monitoring.get("plant_layout"))

    blocking = block_gas + block_weather
    warnings = warn_gas + warn_weather + block_ledger + block_layout

    # 一致性守卫：带仿真痕迹的数据不允许被声明为 REAL
    declared = data_source or monitoring.get("data_source")
    looks_simulated = bool(monitoring.get("simulation_id")) or monitoring.get("data_source") == DATA_SOURCE_SIMULATED
    if looks_simulated and declared == DATA_SOURCE_REAL:
        blocking.append("数据来源标记不一致：数据含仿真标识却声明为 REAL，已拒绝采信")

    penalty = 0.0
    if not ok_ledger:
        penalty += 0.15
    if not ok_layout:
        penalty += 0.10

    checks = [
        {"item": "气体传感器实时读数", "block": "气体浓度", "required": True, "present": ok_gas,
         "detail": "点位/浓度/单位/时间戳齐备" if ok_gas else "；".join(block_gas)},
        {"item": "气象参数", "block": "气象", "required": True, "present": ok_weather,
         "detail": "风向/风速/温湿度齐备" if ok_weather else "；".join(block_weather)},
        {"item": "设备隐患台账", "block": "设备隐患台账", "required": False, "present": ok_ledger,
         "detail": "可用" if ok_ledger else "；".join(block_ledger)},
        {"item": "厂区平面布置", "block": "厂区布置", "required": False, "present": ok_layout,
         "detail": "可用" if ok_layout else "；".join(block_layout)},
    ]

    if blocking:
        status = "insufficient_data"
        summary = "数据不齐备，溯源链路已阻塞：" + "；".join(blocking)
    elif warnings:
        status = "ok_degraded"
        summary = "核心数据齐备，可开展溯源，但存在降级项：" + "；".join(warnings)
    else:
        status = "ok"
        summary = "数据齐备，可执行完整溯源链路（数据校验 → 多源关联 → 根因推断 → 风险研判）。"

    return {
        "status": status,
        "proceed": not blocking,
        "data_source": declared,
        "checks": checks,
        "missing_blocks": [c["block"] for c in checks if not c["present"]],
        "blocking": blocking,
        "warnings": warnings,
        "confidence_penalty": round(penalty, 2),
        "summary": summary,
    }


# ============================================================
# 3. 文本渲染（与现场/模型约定的固定格式一致）
# ============================================================
def _fmt_num(value: Any) -> str:
    num = _num(value)
    if num is None:
        return str(value)
    return str(int(num)) if abs(num - round(num)) < 1e-9 else f"{num:g}"


def format_monitoring_text(monitoring: dict, data_source: str, simulation_id: Optional[str] = None,
                           generated_at: Optional[str] = None, scenario: Optional[str] = None) -> str:
    """把结构化数据渲染成固定文本格式。数据来源行是强制内容，不可省略。"""
    if not monitoring:
        return f"【数据来源】{DATA_SOURCE_LABEL.get(data_source, data_source)}\n{UNAVAILABLE_NOTICE}"

    lines: list[str] = []
    source_label = DATA_SOURCE_LABEL.get(data_source, data_source)
    lines.append(f"【数据来源】{source_label}")
    if data_source == DATA_SOURCE_SIMULATED:
        lines.append(f"【数据说明】{SIMULATION_NOTICE}")
    elif data_source == DATA_SOURCE_REAL:
        lines.append(f"【数据说明】{REAL_NOTICE}")

    head = []
    if simulation_id:
        head.append(f"批次 {simulation_id}")
    if generated_at:
        head.append(f"生成于 {generated_at}")
    if scenario:
        head.append(f"场景 {scenario}")
    if head:
        lines.append("【数据批次】" + " ｜ ".join(head))
    lines.append("")

    # ---- 气体浓度 ----
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

    # ---- 气象 ----
    w = monitoring.get("气象", {})
    lines.append("【气象】")
    deg = w.get("风向角度")
    lines.append(f"- 实时风向：{w.get('风向', '未知')}（{_fmt_num(deg)}°）" if deg is not None
                 else f"- 实时风向：{w.get('风向', '未知')}")
    lines.append(f"- 风速：{_fmt_num(w.get('风速'))} m/s")
    lines.append(f"- 温湿度：{_fmt_num(w.get('温度'))}℃ / {_fmt_num(w.get('湿度'))}%")
    if w.get("采样时间"):
        lines.append(f"- 采样时间：{w['采样时间']}")
    lines.append("")

    # ---- 隐患台账 ----
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

    # ---- 厂区布置 ----
    lines.append("【厂区布置】")
    for area in monitoring.get("厂区布置", []):
        lines.append(f"- {area.get('区域名称', '')}：{area.get('类型', '')}，位于 {area.get('相对位置', '')}")
    return "\n".join(lines)


# ============================================================
# 4. 取数入口（现场数据优先；仿真需显式开启；取不到就如实返回）
# ============================================================
def resolve_mode(mode: Optional[str] = None) -> str:
    raw = (mode or os.getenv(MODE_ENV) or DEFAULT_MODE).strip().lower()
    alias = {"sim": "simulate", "demo": "simulate", "mock": "simulate",
             "none": "off", "disabled": "off", "unavailable": "off"}
    return alias.get(raw, raw)


def _http_provider(endpoint: str) -> Callable[[], dict]:
    def _fetch() -> dict:
        with urllib.request.urlopen(endpoint, timeout=5) as resp:  # noqa: S310 - 内网固定端点
            return json.loads(resp.read().decode("utf-8"))
    return _fetch


def acquire_monitoring_data(
    mode: Optional[str] = None,
    provider: Optional[Callable[[], dict]] = None,
    seed: Optional[int] = None,
    scenario: Optional[str] = None,
    now: Optional[datetime] = None,
    endpoint: Optional[str] = None,
    point_count: int = 6,
) -> dict[str, Any]:
    """获取一批监测数据，返回统一信封（永远带 data_source 与校验结论）。"""
    now = now or datetime.now()
    mode = resolve_mode(mode)
    endpoint = endpoint or os.getenv(ENDPOINT_ENV) or None
    real_provider = provider or (_http_provider(endpoint) if endpoint else None)

    def _envelope(status, data_source, notice, monitoring, text, validation, **extra):
        envelope = {
            "status": status,
            "mode": mode,
            "data_source": data_source,
            "data_source_label": DATA_SOURCE_LABEL[data_source],
            "notice": notice,
            "monitoring": monitoring,
            "text": text,
            "validation": validation,
        }
        envelope.update(extra)
        return envelope

    # ---- 现场数据优先 ----
    if mode in ("real", "auto") and real_provider is not None:
        try:
            payload = real_provider() or {}
            payload.setdefault("data_source", DATA_SOURCE_REAL)
            validation = validate_monitoring_data(payload, DATA_SOURCE_REAL)
            text = format_monitoring_text(payload, DATA_SOURCE_REAL)
            return _envelope("ok" if validation["proceed"] else "no_data",
                             DATA_SOURCE_REAL, REAL_NOTICE, payload, text, validation)
        except Exception as exc:  # 现场源异常时如实上报，不静默降级成仿真
            validation = validate_monitoring_data({}, DATA_SOURCE_UNAVAILABLE)
            validation["blocking"].append(f"现场数据源调用失败：{exc}")
            validation["summary"] = "现场数据源调用失败，未获取到可用数据。"
            return _envelope("no_data", DATA_SOURCE_UNAVAILABLE, UNAVAILABLE_NOTICE, None, None, validation,
                             error=str(exc))

    # ---- 仿真数据（显式开启，输出恒定带 SIMULATED 标记） ----
    if mode in ("simulate", "auto"):
        from . import simulated_data  # 延迟导入，避免循环依赖

        payload = simulated_data.generate_monitoring_data(
            seed=seed, scenario=scenario, now=now, point_count=point_count)
        validation = validate_monitoring_data(payload, DATA_SOURCE_SIMULATED)
        text = format_monitoring_text(
            {k: payload[k] for k in ("气体浓度", "气象", "设备隐患台账", "厂区布置") if k in payload},
            DATA_SOURCE_SIMULATED,
            simulation_id=payload.get("simulation_id"),
            generated_at=payload.get("generated_at"),
            scenario=payload.get("scenario"),
        )
        return _envelope("ok", DATA_SOURCE_SIMULATED, SIMULATION_NOTICE, payload, text, validation,
                         simulation_id=payload.get("simulation_id"),
                         scenario=payload.get("scenario"),
                         scenario_label=payload.get("scenario_label"))

    # ---- 未获取到数据 ----
    validation = validate_monitoring_data({}, DATA_SOURCE_UNAVAILABLE)
    return _envelope("no_data", DATA_SOURCE_UNAVAILABLE, UNAVAILABLE_NOTICE, None, None, validation)


__all__ = [
    "DATA_SOURCE_REAL", "DATA_SOURCE_SIMULATED", "DATA_SOURCE_UNAVAILABLE",
    "DATA_SOURCE_LABEL", "SIMULATION_NOTICE", "REAL_NOTICE", "UNAVAILABLE_NOTICE",
    "GAS_UNITS", "READING_STATUS", "MODE_ENV", "ENDPOINT_ENV",
    "validate_monitoring_data", "format_monitoring_text",
    "acquire_monitoring_data", "resolve_mode",
]
