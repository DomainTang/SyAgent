# -*- coding: utf-8 -*-
"""溯源分析流水线：监测数据 → 样本行 → 模型推理 → 坐标/风况 → 汇总 → 图表 / Excel / 烟羽地图。

输入支持四种形态（见 :func:`resolve_inputs`）：
1. 固定格式监测文本（``get-monitoring-data`` 返回的 ``text``，含【气体浓度】【气象】段落）；
2. 结构化监测数据（``monitoring`` 字典）；
3. 一行一条的样本行（``传感器141，位置为生产指挥中心，风速为2级，风向为东北风``）；
4. 什么都不传：自动获取一批监测数据（默认仿真 SIMULATED）。

输出：逐条预测记录、统计汇总、两张统计图（PNG 字节 + 落盘）、Excel，
以及可选的逐事件烟羽扩散地图 HTML。数据来源标记全程透传。
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, Iterable, Optional

from . import charts, paths, realtime_data, simulated_data
from .model_design import CorpusAnalyzer
from .predictor import get_predictor, preprocess_prediction_text
from .plume_visualization import create_plume_visualization

logger = logging.getLogger(__name__)

# 用户直接给出的样本行：未声明来源，不按仿真数据渲染水印，但仍如实标注
DATA_SOURCE_USER = "USER"
USER_SOURCE_LABEL = "用户提供（未声明数据来源）"

DEFAULT_COORDINATE = ("117.02127280", "30.53173852")
DEFAULT_WIND = ("3", "东风")

CONFIDENCE_LEVELS = ((0.8, "高"), (0.5, "中"))


def data_source_label(source: Optional[str]) -> str:
    if source == DATA_SOURCE_USER:
        return USER_SOURCE_LABEL
    return realtime_data.DATA_SOURCE_LABEL.get(source or "", source or "未标注")


# ============================================================
# 1. 文本解析工具
# ============================================================
def is_sample_line(line: str) -> bool:
    return ("传感器" in line) and ("位置为" in line)


def extract_true_label(text: str) -> Optional[str]:
    """若文本含“疑似源为”，返回其中的真实标签（仅用于对照显示）。"""
    if "疑似源为" in text:
        return text.split("疑似源为")[1].strip()
    return None


def clean_source_name(source: str) -> str:
    """统计时去掉源名称末尾的坐标（与原 GUI 正则行为一致）。"""
    cleaned = re.sub(
        r"\s*[（(]\s*[+-]?\d{1,3}(?:\.\d+)?\s*[,，]\s*"
        r"[+-]?\d{1,3}(?:\.\d+)?\s*[）)]?\s*$",
        "", (source or "").strip()
    ).strip()
    return cleaned


def coordinates_in_text(text: str):
    """从文本中提取坐标（支持中文括号），找不到返回 None。"""
    if not text:
        return None
    try:
        match = re.search(
            r"[（(]\s*([+-]?\d+(?:\.\d+)?)\s*[,，]\s*([+-]?\d+(?:\.\d+)?)\s*[）)]", text)
        if match:
            return match.group(1), match.group(2)
    except Exception:  # noqa: BLE001 - 解析失败按“无坐标”处理
        pass
    return None


def extract_weather_from_input_text(text: str):
    """从样本行提取风速(级)与风向，缺失时返回默认值（与原程序一致）。"""
    wind_speed, wind_direction = DEFAULT_WIND
    try:
        speed_match = re.search(r"风速为\s*([\d\-]+)\s*级", text)
        if speed_match:
            wind_speed = speed_match.group(1)
        direction_match = re.search(r"风向为\s*([东南西北东北东南西南西北]+风)", text)
        if direction_match:
            wind_direction = direction_match.group(1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("提取风速风向失败: %s", exc)
    return wind_speed, wind_direction


def _detect_source_from_text(text: str) -> Optional[str]:
    """从固定格式文本的【数据来源】行识别来源标记。"""
    if "SIMULATED" in text or "本地仿真" in text or "仿真数据" in text:
        return realtime_data.DATA_SOURCE_SIMULATED
    if "REAL" in text or "现场实测" in text:
        return realtime_data.DATA_SOURCE_REAL
    return None


_READING_LINE_RE = re.compile(r"^-\s*点位\s*(?P<pid>[^（(]+?)\s*[（(](?P<meta>[^）)]*)[）)]")
_WIND_DIR_RE = re.compile(r"-\s*实时风向：\s*(?P<direction>[^（(\s]+)")
_WIND_SPEED_RE = re.compile(r"-\s*风速：\s*(?P<speed>-?\d+(?:\.\d+)?)\s*m/s")
_SIM_ID_RE = re.compile(r"批次\s*(?P<sid>[A-Za-z0-9\-_]+)")
_SCENARIO_RE = re.compile(r"场景\s*(?P<scenario>[A-Za-z_]+)")


def parse_monitoring_text(text: str) -> dict:
    """解析固定格式监测文本，抽出点位读数与气象参数。"""
    readings: list = []
    weather: dict = {}

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        reading = _READING_LINE_RE.match(line)
        if reading:
            meta = [p.strip() for p in reading.group("meta").split("，") if p.strip()]
            readings.append({
                "点位编号": reading.group("pid").strip(),
                "传感器类型": meta[0] if meta else "",
                "位置": meta[1] if len(meta) > 1 else "",
            })
            continue
        direction = _WIND_DIR_RE.match(line)
        if direction:
            weather["风向"] = direction.group("direction")
            continue
        speed = _WIND_SPEED_RE.match(line)
        if speed:
            speed_ms = float(speed.group("speed"))
            weather["风速"] = speed_ms
            weather["风速级"] = simulated_data.wind_speed_level(speed_ms)

    return {
        "readings": readings,
        "weather": weather,
        "data_source": _detect_source_from_text(text or ""),
        "simulation_id": (m.group("sid") if (m := _SIM_ID_RE.search(text or "")) else None),
        "scenario": (m.group("scenario") if (m := _SCENARIO_RE.search(text or "")) else None),
    }


def sample_lines_from_readings(readings: Iterable[dict], weather: Optional[dict] = None) -> list:
    """把点位读数拼成模型可消费的样本行（与原语料库文本同构）。"""
    weather = weather or {}
    wind_speed = weather.get("风速级") or simulated_data.wind_speed_level(weather.get("风速", 3.0))
    wind_direction = weather.get("风向") or DEFAULT_WIND[1]

    lines = []
    for item in readings:
        point = item.get("点位编号")
        location = item.get("位置")
        if not point or not location:
            continue
        lines.append(f"传感器{point}，位置为{location}，风速为{wind_speed}级，风向为{wind_direction}")
    return lines


# ============================================================
# 2. 输入归一化
# ============================================================
def resolve_inputs(data=None, seed: Optional[int] = None, scenario: Optional[str] = None,
                   point_count: int = 6) -> dict:
    """把四种输入形态统一成 {sample_lines, monitoring, validation, data_source, ...}。"""
    result: dict = {
        "sample_lines": [],
        "monitoring": None,
        "validation": None,
        "data_source": None,
        "simulation_id": None,
        "scenario": None,
        "seed": seed,
        "input_mode": "auto",
        "note": "",
        "batch_text": None,
        "error": None,
    }

    if data is None:
        envelope = realtime_data.acquire_monitoring_data(
            seed=seed, scenario=scenario, point_count=point_count)
        result.update({
            "data_source": envelope["data_source"],
            "monitoring": envelope.get("monitoring"),
            "validation": envelope.get("validation"),
            "simulation_id": envelope.get("simulation_id"),
            "scenario": envelope.get("scenario"),
            "batch_text": envelope.get("text"),
            "input_mode": "simulated" if envelope["status"] == "ok" else "unavailable",
            "note": envelope.get("notice", ""),
        })
        if envelope["status"] != "ok" or not envelope.get("monitoring"):
            result["error"] = (envelope.get("validation") or {}).get("summary") or "未获取到监测数据"
            return result
        result["sample_lines"] = simulated_data.build_sample_lines(envelope["monitoring"])
        return result

    if isinstance(data, dict):
        monitoring = data.get("monitoring") if isinstance(data.get("monitoring"), dict) else data
        source = (data.get("data_source") or monitoring.get("data_source")
                  or (realtime_data.DATA_SOURCE_SIMULATED if monitoring.get("simulation_id") else None)
                  or DATA_SOURCE_USER)
        result.update({
            "data_source": source,
            "monitoring": monitoring,
            "simulation_id": data.get("simulation_id") or monitoring.get("simulation_id"),
            "scenario": data.get("scenario") or monitoring.get("scenario"),
            "batch_text": data.get("text"),
            "input_mode": "monitoring",
            "note": data.get("notice", ""),
        })
        if monitoring.get("气体浓度"):
            result["validation"] = realtime_data.validate_monitoring_data(monitoring, source)
            result["sample_lines"] = simulated_data.build_sample_lines(monitoring)
        elif data.get("sample_lines"):
            result["sample_lines"] = [str(x) for x in data["sample_lines"]]
        elif data.get("text"):
            parsed = parse_monitoring_text(str(data["text"]))
            result["sample_lines"] = sample_lines_from_readings(parsed["readings"], parsed["weather"])
        return result

    if isinstance(data, (list, tuple)):
        result.update({
            "data_source": DATA_SOURCE_USER,
            "input_mode": "sample_lines",
            "sample_lines": [str(x) for x in data],
        })
        return result

    # ---- 字符串：固定格式监测文本 或 一行一条的样本行 ----
    text = str(data)
    stripped = text.strip()
    if stripped[:1] in ("{", "["):
        # 客户端可能把上一个工具返回的 JSON 整段回传，这里自动识别并展开
        try:
            decoded = json.loads(stripped)
        except ValueError:
            decoded = None
        if decoded is not None and not isinstance(decoded, str):
            return resolve_inputs(decoded, seed=seed, scenario=scenario, point_count=point_count)

    # 单行样本行长度有限；几百字符以上的行基本是 JSON/日志，不作为样本
    lines = [line.strip() for line in text.splitlines()
             if is_sample_line(line) and len(line) <= 300]
    parsed = parse_monitoring_text(text)
    result.update({
        "data_source": parsed["data_source"] or DATA_SOURCE_USER,
        "input_mode": "monitoring_text" if parsed["readings"] else "sample_lines",
        "simulation_id": parsed["simulation_id"],
        "scenario": parsed["scenario"],
    })
    if lines:
        result["sample_lines"] = lines  # 样本行自带风速/风向
    else:
        result["sample_lines"] = sample_lines_from_readings(parsed["readings"], parsed["weather"])
        if parsed["readings"]:
            result["monitoring"] = {"气体浓度": parsed["readings"], "气象": parsed["weather"]}
    if not result["sample_lines"]:
        result["error"] = "没有解析到可用样本行（需要含“传感器”与“位置为”的行，或固定格式监测文本）"
    return result


# ============================================================
# 3. 推理与汇总
# ============================================================
def load_corpus() -> Optional[CorpusAnalyzer]:
    """加载语料库（用于把疑似源名称换算成经纬度）；缺失时返回 None。"""
    if not paths.CORPUS_FILE.exists():
        logger.warning("语料库不存在，坐标将使用默认值: %s", paths.CORPUS_FILE)
        return None
    try:
        return CorpusAnalyzer(corpus_file=str(paths.CORPUS_FILE))
    except Exception as exc:  # noqa: BLE001 - 语料异常不影响识别
        logger.warning("语料库加载失败，坐标将使用默认值: %s", exc)
        return None


def resolve_coordinates(corpus: Optional[CorpusAnalyzer], source: str):
    """疑似源坐标：标签自带坐标 → 语料库 → 默认坐标。"""
    coords = coordinates_in_text(source)
    if coords:
        return coords
    if corpus is not None:
        try:
            for record in corpus.get_source_weather_info(source) or []:
                coords = coordinates_in_text(record.get("full_text", ""))
                if coords:
                    return coords
        except Exception as exc:  # noqa: BLE001 - 单条解析失败不刷屏
            logger.warning("语料库坐标解析失败（%s）: %s", source, exc)
    return DEFAULT_COORDINATE


def confidence_level(confidence: float) -> str:
    for threshold, label in CONFIDENCE_LEVELS:
        if confidence > threshold:
            return label
    return "低"


def analyze_lines(lines: Iterable[str], data_source: Optional[str],
                  corpus: Optional[CorpusAnalyzer] = None, top_k: int = 3,
                  batch_weather: Optional[dict] = None) -> list:
    """逐条推理：返回结构化记录列表（含坐标、风况、Top-K）。"""
    predictor = get_predictor()
    batch_weather = batch_weather or {}

    records: list = []
    for index, raw_line in enumerate(lines, 1):
        line = (raw_line or "").strip()
        if not line:
            continue
        processed = preprocess_prediction_text(line)
        prediction = predictor.predict(processed, top_k=top_k)
        wind_speed, wind_direction = extract_weather_from_input_text(line)
        if wind_speed == DEFAULT_WIND[0] and batch_weather.get("风速级"):
            wind_speed = batch_weather["风速级"]
        if wind_direction == DEFAULT_WIND[1] and batch_weather.get("风向"):
            wind_direction = batch_weather["风向"]

        record = {
            "index": index,
            "input_text": line,
            "processed_text": processed,
            "wind_speed": wind_speed,
            "wind_direction": wind_direction,
            "true_label": extract_true_label(line),
            "data_source": data_source,
            "status": prediction.get("status"),
        }

        if prediction.get("status") != "success":
            record.update({
                "predicted_source": None,
                "predicted_source_name": None,
                "confidence": None,
                "confidence_level": None,
                "top3": [],
                "longitude": DEFAULT_COORDINATE[0],
                "latitude": DEFAULT_COORDINATE[1],
                "error": prediction.get("error_message", "推理失败"),
            })
            records.append(record)
            continue

        source = prediction["predicted_source"]
        longitude, latitude = resolve_coordinates(corpus, source)
        confidence = float(prediction["confidence"])
        record.update({
            "predicted_source": source,
            "predicted_source_name": clean_source_name(source),
            "confidence": round(confidence, 4),
            "confidence_level": confidence_level(confidence),
            "top3": [
                {"source": name, "confidence": round(float(prob), 4)}
                for name, prob in prediction.get("top3_results", [])
            ],
            "longitude": longitude,
            "latitude": latitude,
        })
        records.append(record)
    return records


def summarize(records: Iterable[dict]) -> dict:
    """统计预测源分布与置信度分布（对应原 GUI 的统计信息）。"""
    records = list(records)
    successful = [r for r in records if r.get("status") == "success"]

    counter: dict = {}
    for rec in successful:
        name = rec.get("predicted_source_name") or "未知"
        counter[name] = counter.get(name, 0) + 1

    confidences = [float(r["confidence"]) for r in successful if r.get("confidence") is not None]
    confidence_stats = None
    if confidences:
        confidence_stats = {
            "count": len(confidences),
            "mean": round(sum(confidences) / len(confidences), 4),
            "min": round(min(confidences), 4),
            "max": round(max(confidences), 4),
            "high": sum(1 for c in confidences if c > 0.8),
            "mid": sum(1 for c in confidences if 0.5 < c <= 0.8),
            "low": sum(1 for c in confidences if c <= 0.5),
        }

    return {
        "count": len(records),
        "success_count": len(successful),
        "error_count": len(records) - len(successful),
        "source_distribution": [
            {"source": name, "count": count}
            for name, count in sorted(counter.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "confidence": confidence_stats,
    }


# ============================================================
# 4. 产物导出（Excel / 烟羽地图）
# ============================================================
def result_rows(records: Iterable[dict], source_label: str) -> list:
    """整理成 Excel 行（列名与原 GUI / 烟羽地图读取逻辑保持一致）。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for rec in records:
        if rec.get("status") != "success":
            rows.append({
                "预测时间": now_str,
                "数据来源": source_label,
                "传感器信息": rec.get("input_text", ""),
                "预测疑似源": "预测错误",
                "疑似源分析": "",
                "经度": rec.get("longitude", ""),
                "纬度": rec.get("latitude", ""),
                "风速(级)": rec.get("wind_speed", ""),
                "风向": rec.get("wind_direction", ""),
                "置信度": "N/A",
                "原始记录": rec.get("error", ""),
            })
            continue
        analysis_text = (f"{rec['input_text']}，疑似源为{rec['predicted_source_name']}"
                         f"({rec['longitude']},{rec['latitude']})")
        rows.append({
            "预测时间": now_str,
            "数据来源": source_label,
            "传感器信息": rec["input_text"],
            "预测疑似源": rec["predicted_source"],
            "疑似源分析": analysis_text,
            "经度": rec["longitude"],
            "纬度": rec["latitude"],
            "风速(级)": rec["wind_speed"],
            "风向": rec["wind_direction"],
            "置信度": f"{rec['confidence']:.4f}",
            "原始记录": analysis_text,
        })
    return rows


def write_excel(rows: list, output_path) -> Optional[str]:
    """写出预测结果 Excel；缺少 openpyxl 时返回 None（不阻塞分析结果）。"""
    import pandas as pd

    try:
        import openpyxl  # noqa: F401 - 仅用于确认引擎可用
    except ImportError:
        logger.warning("未安装 openpyxl，已跳过 Excel 导出")
        return None
    path = str(output_path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    pd.DataFrame(rows).to_excel(path, index=False, sheet_name="预测结果")
    logger.info("预测结果已保存到 Excel: %s", path)
    return path


def write_placeholder_kml(path, monitoring: Optional[dict], base_lon: float = 117.0300,
                          base_lat: float = 30.5300) -> str:
    """现场 KML 缺失时生成示例底图，让烟羽扩散图能渲染。

    所有点位/分区名称一律带“示例”前缀，避免被误当成真实厂区坐标。
    """
    areas = (monitoring or {}).get("厂区布置") or [
        {"区域名称": "示例区域", "类型": "装置区", "相对位置": "示例"}
    ]
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', "<kml><Document>"]
    parts.append("<name>示例点位底图（仿真占位，非真实厂区坐标）</name>")
    for i, area in enumerate(areas):
        lon = base_lon + (i % 3) * 0.004
        lat = base_lat - (i // 3) * 0.004
        name = f"示例点位-{area.get('区域名称', i + 1)}"
        parts.append(
            f"<Placemark><name>{name}</name>"
            f"<description>示例底图占位点位（仿真生成），类型：{area.get('类型', '')}</description>"
            f"<Point><coordinates>{lon:.6f},{lat:.6f},0</coordinates></Point>"
            f"<Polygon><outerBoundaryIs><LinearRing><coordinates>"
            f"{lon - 0.0015:.6f},{lat - 0.0015:.6f},0 "
            f"{lon + 0.0015:.6f},{lat - 0.0015:.6f},0 "
            f"{lon + 0.0015:.6f},{lat + 0.0015:.6f},0 "
            f"{lon - 0.0015:.6f},{lat + 0.0015:.6f},0 "
            f"{lon - 0.0015:.6f},{lat - 0.0015:.6f},0"
            f"</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
        )
    parts.append("</Document></kml>")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(parts))
    return str(path)


def generate_plume_maps(kml_path, excel_path, plume_dir, max_events: Optional[int] = None,
                        data_source_label_text: Optional[str] = None) -> list:
    """逐事件生成烟羽扩散 HTML，并写一个总览 index.html。"""
    if not (kml_path and os.path.exists(kml_path)):
        logger.warning("KML 文件不存在，跳过烟羽可视化: %s", kml_path)
        return []

    import pandas as pd

    try:
        df = pd.read_excel(excel_path, engine="openpyxl")
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取预测结果失败，跳过烟羽可视化: %s", exc)
        return []
    if df.empty:
        logger.warning("预测结果为空，跳过烟羽可视化")
        return []

    os.makedirs(plume_dir, exist_ok=True)
    total = len(df)
    count = total if not max_events or max_events <= 0 else min(int(max_events), total)

    produced: list = []
    for idx in range(count):
        out_html = os.path.join(plume_dir, f"event_{idx + 1:03d}.html")
        try:
            # 原可视化模块大量 print 到 stdout；stdio 传输下 stdout 是协议通道，
            # 这里统一重定向到 stderr：日志照常可见，MCP 协议不被破坏。
            with contextlib.redirect_stdout(sys.stderr):
                map_obj = create_plume_visualization(
                    kml_path=kml_path,
                    excel_path=excel_path,
                    output_html_path=out_html,
                    current_column_index=idx,
                    data_source=data_source_label_text,
                )
            if map_obj and os.path.exists(out_html):
                produced.append(out_html)
            else:
                logger.warning("事件 %d 烟羽可视化生成失败", idx + 1)
        except Exception as exc:  # noqa: BLE001 - 单事件失败不影响整体结果
            logger.warning("事件 %d 烟羽可视化异常: %s", idx + 1, exc)

    if produced:
        index_path = os.path.join(plume_dir, "index.html")
        cards = [
            f'<li><a href="{os.path.basename(html)}">事件{i}: 查看烟羽扩散地图</a></li>'
            for i, html in enumerate(produced, 1)
        ]
        banner = (
            '<p style="padding:10px;border:2px solid #c0392b;color:#c0392b;font-weight:bold;">'
            f'本页所有地图的数据来源为 {data_source_label_text}，非现场实测，严禁用于现场处置决策。</p>'
        ) if data_source_label_text else ""
        html_body = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>烟羽扩散可视化 - 事件总览</title></head><body style="font-family:Microsoft YaHei;">
<h2>烟羽扩散可视化事件总览</h2>
{banner}
<p>共 {len(produced)} 个事件，点击查看对应烟羽扩散地图（地图瓦片需联网加载）。</p>
<ol>{''.join(cards)}</ol></body></html>"""
        with open(index_path, "w", encoding="utf-8") as fp:
            fp.write(html_body)
        produced.append(index_path)
    return produced


# ============================================================
# 5. 结果文本（给模型/人看的摘要，与旧版终端输出对应）
# ============================================================
def format_analysis_text(payload: dict) -> str:
    """把分析结果渲染成可读文本（MCP 返回的 text 内容）。"""
    lines: list = []
    lines.append("=" * 56)
    lines.append("石化疑似源溯源分析结果")
    lines.append("=" * 56)
    lines.append(f"数据来源：{payload['data_source_label']}")
    if payload.get("notice"):
        lines.append(f"数据说明：{payload['notice']}")
    batch = []
    if payload.get("simulation_id"):
        batch.append(f"批次 {payload['simulation_id']}")
    if payload.get("scenario"):
        batch.append(f"场景 {payload['scenario']}")
    batch.append(f"样本数 {payload['summary']['count']}")
    lines.append("批次信息：" + " ｜ ".join(batch))
    if payload.get("validation"):
        lines.append(f"数据校验：{payload['validation']['summary']}")
    lines.append("")

    lines.append("-" * 56)
    lines.append("逐点位预测结果")
    lines.append("-" * 56)
    for rec in payload["results"]:
        if rec["status"] != "success":
            lines.append(f"[{rec['index']}] {rec['input_text']}")
            lines.append(f"    预测失败：{rec.get('error', '')}")
            continue
        lines.append(f"[{rec['index']}] {rec['input_text']}")
        lines.append(
            f"    疑似源：{rec['predicted_source_name']}（{rec['longitude']}, {rec['latitude']}）"
            f" ｜ 置信度：{rec['confidence']:.4f}（{rec['confidence_level']}置信度）"
        )
        top3 = "，".join(f"{item['source']} {item['confidence']:.4f}" for item in rec["top3"])
        if top3:
            lines.append(f"    Top3：{top3}")
        if rec.get("true_label"):
            same = clean_source_name(rec["true_label"]) == rec["predicted_source_name"]
            lines.append(f"    真实标签（对照）：{rec['true_label']}  [{'一致' if same else '不一致'}]")
    lines.append("")

    summary = payload["summary"]
    lines.append("-" * 56)
    lines.append("预测源分布")
    lines.append("-" * 56)
    for item in summary["source_distribution"][:12]:
        lines.append(f"  {item['source']}: {item['count']}")
    lines.append(f"  合计：{summary['success_count']} 条有效预测"
                 + (f"，{summary['error_count']} 条失败" if summary["error_count"] else ""))
    lines.append("")

    confidence = summary.get("confidence")
    if confidence:
        lines.append("-" * 56)
        lines.append("置信度统计")
        lines.append("-" * 56)
        lines.append(f"  样本数 {confidence['count']} ｜ 均值 {confidence['mean']:.4f}"
                     f" ｜ 最小 {confidence['min']:.4f} ｜ 最大 {confidence['max']:.4f}")
        lines.append(f"  高置信度(>0.8)：{confidence['high']}"
                     f" ｜ 中置信度(0.5~0.8)：{confidence['mid']}"
                     f" ｜ 低置信度(<=0.5)：{confidence['low']}")
        lines.append("")

    artifacts = payload.get("artifacts", {})
    lines.append("-" * 56)
    lines.append("本次绘制的图（已随本结果以图片形式返回）")
    lines.append("-" * 56)
    for chart in artifacts.get("charts", []):
        lines.append(f"  {chart['name']}：{chart['path']}")
    for plume in artifacts.get("plume_maps", []):
        lines.append(f"  烟羽扩散地图：{plume}")
    if artifacts.get("excel"):
        lines.append(f"  预测结果 Excel：{artifacts['excel']}")
    lines.append("")
    if payload.get("disclaimer"):
        lines.append(payload["disclaimer"])
    return "\n".join(lines)


# ============================================================
# 6. 对外主入口
# ============================================================
def run_analysis(data=None, seed: Optional[int] = None, scenario: Optional[str] = None,
                 point_count: int = 6, top_k: int = 3, plume_events: int = 0,
                 output_root=None, run_dir=None) -> dict:
    """执行一次完整溯源分析，返回可直接作为 MCP 结构化结果的字典。"""
    inputs = resolve_inputs(data, seed=seed, scenario=scenario, point_count=point_count)
    source = inputs["data_source"]
    source_text = data_source_label(source)
    simulated = source == realtime_data.DATA_SOURCE_SIMULATED

    payload: dict = {
        "status": "success",
        "data_source": source,
        "data_source_label": source_text,
        "notice": inputs.get("note") or "",
        "simulation_id": inputs.get("simulation_id"),
        "scenario": inputs.get("scenario"),
        "seed": inputs.get("seed"),
        "input": {"mode": inputs["input_mode"], "sample_count": len(inputs["sample_lines"])},
        "validation": inputs.get("validation"),
        "results": [],
        "summary": {},
        "artifacts": {"run_dir": None, "excel": None, "charts": [], "plume_maps": []},
    }

    if inputs.get("error") or not inputs["sample_lines"]:
        payload["status"] = "error"
        payload["error"] = inputs.get("error") or "没有可分析的样本行"
        payload["disclaimer"] = (
            realtime_data.UNAVAILABLE_NOTICE
            if source == realtime_data.DATA_SOURCE_UNAVAILABLE else ""
        )
        payload["text_report"] = format_analysis_text(payload) if payload["results"] else (
            f"溯源分析未执行：{payload['error']}\n{payload['disclaimer']}"
        )
        return payload

    try:
        get_predictor()  # 首次调用时才加载模型权重
    except Exception as exc:  # noqa: BLE001 - 模型缺失/加载失败要如实回报
        payload["status"] = "error"
        payload["error"] = f"模型加载失败：{exc}"
        payload["text_report"] = payload["error"]
        return payload

    corpus = load_corpus()
    monitoring = inputs.get("monitoring")
    batch_weather = monitoring.get("气象") if isinstance(monitoring, dict) else None
    payload["results"] = analyze_lines(
        inputs["sample_lines"], source, corpus=corpus, top_k=top_k, batch_weather=batch_weather)
    payload["summary"] = summarize(payload["results"])
    logger.info("溯源分析完成：%s 条样本，来源 %s", payload["summary"]["count"], source_text)

    # ---- 产物落盘：Excel → 统计图 → 烟羽地图 ----
    run_path = run_dir or paths.new_run_dir(output_root)
    rows = result_rows(payload["results"], source_text)
    excel_path = write_excel(rows, os.path.join(str(run_path), "预测结果.xlsx"))
    payload["artifacts"]["run_dir"] = str(run_path)
    payload["artifacts"]["excel"] = excel_path

    chart_specs = [
        ("预测源分布_Top12", "预测源分布_Top12.png", charts.plot_source_distribution),
        ("置信度分布", "置信度分布.png", charts.plot_confidence_distribution),
    ]
    for name, filename, plotter in chart_specs:
        chart_path = os.path.join(str(run_path), filename)
        png = plotter(payload["results"], output_path=chart_path, simulated=simulated)
        if png:
            payload["artifacts"]["charts"].append({"name": name, "path": chart_path})

    if plume_events and excel_path:
        kml_path = str(paths.KML_FILE) if paths.KML_FILE.exists() else write_placeholder_kml(
            os.path.join(str(run_path), "示例点位底图.kml"), monitoring)
        if not paths.KML_FILE.exists():
            logger.warning("现场 KML 缺失，已生成示例点位底图（非真实厂区坐标）: %s", kml_path)
        payload["artifacts"]["plume_maps"] = generate_plume_maps(
            kml_path, excel_path, os.path.join(str(run_path), "plume"),
            max_events=plume_events, data_source_label_text=source_text)

    payload["disclaimer"] = (
        realtime_data.SIMULATION_NOTICE if simulated
        else (realtime_data.REAL_NOTICE if source == realtime_data.DATA_SOURCE_REAL else "")
    )
    payload["text_report"] = format_analysis_text(payload)
    return payload


__all__ = [
    "run_analysis", "resolve_inputs", "analyze_lines", "summarize", "result_rows",
    "format_analysis_text", "parse_monitoring_text", "sample_lines_from_readings",
    "clean_source_name", "data_source_label", "DATA_SOURCE_USER",
]
