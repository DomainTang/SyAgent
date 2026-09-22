# -*- coding: utf-8 -*-
"""溯源分析结果统计图：预测源分布（Top 12）与置信度分布。

对应原 Qt 界面的两张统计图。函数直接返回 PNG 字节，MCP 工具可以把图片内联返回给
客户端；同时可选落盘到 output/ 目录。仿真数据会叠加水印，避免统计结果被当成
现场实测结论使用。
"""
from __future__ import annotations

import io
import logging
import os
from typing import Optional, Sequence

import matplotlib

matplotlib.use("Agg")  # 无界面后端：容器 / 服务端环境必须

import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)

# 原 GUI 配色（终端与图表保持一致）
SECONDARY_COLOR = "#3498db"
SIMULATION_WATERMARK = "模拟数据 SIMULATED"

# Matplotlib 中文字体设置（与原工程保持一致）
plt.rcParams["font.sans-serif"] = [
    "SimHei", "Microsoft YaHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "DejaVu Sans",
]
plt.rcParams["axes.unicode_minus"] = False


def add_simulation_watermark(fig, text: str = SIMULATION_WATERMARK) -> None:
    """在图上叠加仿真水印：仿真结果禁止被当作现场实测结论使用。"""
    fig.text(0.5, 0.5, text, fontsize=30, color="#c0392b", alpha=0.16,
             ha="center", va="center", rotation=20, zorder=10)


def _finish(fig, output_path) -> bytes:
    """保存（可选）并返回 PNG 字节。"""
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=150, bbox_inches="tight")
    if output_path:
        path = str(output_path)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fig.savefig(path, dpi=150, bbox_inches="tight")
        logger.info("统计图已保存: %s", path)
    plt.close(fig)
    return buffer.getvalue()


def _successful(records: Sequence[dict]) -> list[dict]:
    return [r for r in records if r.get("status") == "success"]


def plot_source_distribution(records: Sequence[dict], output_path=None,
                             simulated: bool = False):
    """柱状图：预测源分布 Top-12（复刻原 update_chart）。无有效结果时返回 None。"""
    counter: dict = {}
    for rec in _successful(records):
        name = rec.get("predicted_source_name") or rec.get("predicted_source") or "未知"
        counter[name] = counter.get(name, 0) + 1
    if not counter:
        logger.warning("没有成功的预测结果，跳过预测源分布图")
        return None

    top_sources = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:12]
    names = [n for n, _ in top_sources]
    values = [v for _, v in top_sources]

    fig, ax = plt.subplots(figsize=(11, 6))
    bars = ax.bar(names, values, color=SECONDARY_COLOR)
    ax.set_title("预测源分布（Top 12）" + ("  [模拟数据 SIMULATED]" if simulated else ""))
    ax.set_xlabel("预测源")
    ax.set_ylabel("数量")
    ax.tick_params(axis="x", labelrotation=30, labelsize=9)
    for label in ax.get_xticklabels():
        label.set_horizontalalignment("right")
    ax.bar_label(bars, padding=3, fontsize=9)
    ax.set_ylim(0, max(values) * 1.12)
    ax.grid(axis="y", linestyle="--", alpha=0.3)
    if simulated:
        add_simulation_watermark(fig)
    fig.tight_layout()
    return _finish(fig, output_path)


def plot_confidence_distribution(records: Sequence[dict], output_path=None,
                                 simulated: bool = False):
    """直方图：置信度分布 bins=10（复刻原 update_chart）。无有效结果时返回 None。"""
    confidences = [float(r["confidence"]) for r in _successful(records)
                   if r.get("confidence") is not None]
    if not confidences:
        logger.warning("没有成功的预测结果，跳过置信度分布图")
        return None

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(confidences, bins=10, range=(0, 1), edgecolor="black")
    ax.set_title("置信度分布" + ("  [模拟数据 SIMULATED]" if simulated else ""))
    ax.set_xlabel("置信度")
    ax.set_ylabel("数量")
    ax.set_xlim(0, 1)
    if simulated:
        add_simulation_watermark(fig)
    fig.tight_layout()
    return _finish(fig, output_path)


__all__ = [
    "add_simulation_watermark", "plot_source_distribution",
    "plot_confidence_distribution", "SIMULATION_WATERMARK",
]
