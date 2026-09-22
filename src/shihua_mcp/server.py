#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""石化疑似源识别 MCP 服务（Shihua VOC MCP Server）。

把「数据生成」与「溯源分析」封装成两个标准 MCP 工具，供 Cursor、Cherry Studio、
通义灵码、Claude、Dify 等支持 MCP 的客户端调用（stdio 本地接入，或由平台托管）：

| 工具 | 说明 |
| --- | --- |
| ``get-monitoring-data`` | 生成一批厂区多源监测数据（气体浓度 / 气象 / 隐患台账 / 厂区布置） |
| ``analyze-source`` | 对监测数据做疑似源溯源分析，并绘制预测源分布图、置信度分布图 |

启动方式：

```bash
python -m shihua_mcp.server                     # stdio（MCP 客户端 / 平台托管，推荐）
python -m shihua_mcp.server --self-test         # 自检：列工具 + 跑一遍取数与溯源，不起服务
python -m shihua_mcp.server --http --port 8000  # Streamable HTTP + SSE（容器 / 云端）
```

环境变量：``MCP_TRANSPORT``（stdio/http/sse）、``HOST`` / ``PORT``、
``SHIHUA_DATA_MODE``（simulate/real/auto/off）、``SHIHUA_REALTIME_ENDPOINT``（现场数据源）、
``SHIHUA_MODELS_DIR`` / ``SHIHUA_DATA_DIR`` / ``SHIHUA_OUTPUT_DIR``。

注意：stdio 模式下 stdout 是 JSON-RPC 通道，本服务所有日志一律走 stderr。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Optional

# ---------------------------------------------------------------------------
# 容器 / 受限环境自愈：必须在 import fastmcp 之前执行
# ---------------------------------------------------------------------------
# fastmcp 默认把版本缓存写到用户数据目录（Windows: %LOCALAPPDATA%\fastmcp，
# Linux: ~/.local/share/fastmcp）。在 Docker、只读 HOME、Windows 服务账号下，
# 这一步会抛 PermissionError 让服务在启动阶段直接退出，客户端表现就是"拿不到工具"。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("FASTMCP_HOME", str(PROJECT_ROOT / "output" / ".fastmcp"))
os.environ.setdefault("FASTMCP_CHECK_FOR_UPDATES", "off")
os.environ.setdefault("FASTMCP_SHOW_SERVER_BANNER", "false")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mcp.types as mcp_types  # noqa: E402
from fastmcp import FastMCP  # noqa: E402
from fastmcp.tools import ToolResult  # noqa: E402
from fastmcp.utilities.types import Image as MCPImage  # noqa: E402
from pydantic import Field  # noqa: E402

from app import analysis, realtime_data, simulated_data  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("shihua_mcp")

VERSION = "0.2.0"
SERVER_NAME = "shihua-voc-mcp"

DEFAULT_HTTP_HOST = "0.0.0.0"
DEFAULT_HTTP_PORT = 8000
DEFAULT_HTTP_PATH = "/mcp"

SCENARIO_VALUES = "auto / normal / leak / leak_multi / sensor_fault"

SERVER_INSTRUCTIONS = (
    "本服务用于石化厂区可燃气体/VOC 泄漏的疑似源识别与溯源分析。请按以下顺序使用工具：\n"
    "1. 需要监测数据时调用 `get-monitoring-data`（不要向用户索要数据，也不要自行编造数值）；\n"
    "2. 拿到数据后调用 `analyze-source` 做溯源分析；`analyze-source` 也可以不传数据，"
    "它会自行取一批数据并出图。\n"
    "回答时必须如实引用返回的 data_source 标记：SIMULATED 表示本地仿真数据，"
    "必须说明“本结论基于本地仿真数据，非现场实测，不得用于现场处置决策”。"
)

mcp = FastMCP(
    name=SERVER_NAME,
    version=VERSION,
    instructions=SERVER_INSTRUCTIONS,
)

GET_MONITORING_DATA_DESCRIPTION = (
    "获取一批厂区多源监测数据（气体浓度 / 气象 / 设备隐患台账 / 厂区布置）。"
    "需要监测数据时直接调用本工具，不要向用户索要、也不要自行编造任何数值。\n\n"
    "返回内容：\n"
    "- text：固定格式监测文本（首行【数据来源】），可直接作为 `analyze-source` 的输入；\n"
    "- sample_lines：一行一条的样本行，也可直接传给 `analyze-source`；\n"
    "- monitoring：结构化数据；validation：程序化齐备性结论（proceed=False 时不得输出溯源结论）；\n"
    "- data_source：SIMULATED（本地仿真）/ REAL（现场实测）/ UNAVAILABLE（未取到）。\n\n"
    "默认生成仿真数据：数据体、文本首行与后续图表都会如实标注 SIMULATED，"
    "仅可用于系统联调、流程演示与故障演练，严禁用于现场处置决策。"
)

ANALYZE_SOURCE_DESCRIPTION = (
    "对厂区监测数据做疑似源溯源分析：逐点位识别疑似源（含经纬度、置信度、Top3 候选），"
    "给出预测源分布与置信度统计，并绘制两张统计图（预测源分布 Top12、置信度分布）"
    "随结果以图片形式一并返回。\n\n"
    "data 可以传：\n"
    "- `get-monitoring-data` 返回的 text（固定格式监测文本，推荐）；\n"
    "- `get-monitoring-data` 返回的 monitoring（结构化对象）或 sample_lines（样本行数组）；\n"
    "- 一行一条的样本行文本，例如“传感器141，位置为生产指挥中心，风速为2级，风向为东北风”；\n"
    "- 省略不传：自动获取一批监测数据后直接分析（适合演示）。\n\n"
    "plume_events > 0 时，额外生成前 N 个事件的烟羽扩散地图 HTML（写入服务端输出目录并返回路径）。\n"
    "结果中的 data_source 必须如实引用：SIMULATED 为本地仿真数据，不得当作现场实测结论使用。"
)


# ============================================================
# 工具 1：数据生成
# ============================================================
@mcp.tool(name="get-monitoring-data", description=GET_MONITORING_DATA_DESCRIPTION)
def get_monitoring_data(
    scenario: Annotated[
        str,
        Field(description=f"监测场景（默认 auto 随机）：{SCENARIO_VALUES}。"
                          "leak=单点异常泄漏，leak_multi=多点异常，sensor_fault=仪表异常"),
    ] = "auto",
    point_count: Annotated[int, Field(description="监测点位数量，3~12，默认 6")] = 6,
    seed: Annotated[Optional[int], Field(description="随机种子；指定后同一批次数据可复现")] = None,
) -> dict:
    """获取一批厂区多源监测数据（默认仿真数据，恒定带 SIMULATED 标记）。"""
    if scenario and scenario not in ("auto", *simulated_data.SCENARIOS):
        return {
            "status": "error",
            "error": f"未知场景 {scenario!r}，可选值：{SCENARIO_VALUES}",
        }

    envelope = realtime_data.acquire_monitoring_data(
        seed=seed, scenario=scenario, point_count=point_count)
    monitoring = envelope.get("monitoring")
    envelope["sample_lines"] = (
        simulated_data.build_sample_lines(monitoring) if monitoring else []
    )
    if envelope["status"] == "ok":
        envelope["next_step"] = (
            "把 text 或 sample_lines 传给 analyze-source 即可得到疑似源识别结果与统计图；"
            "若 data_source=SIMULATED，结论中必须声明其为仿真数据。"
        )
    return envelope


# ============================================================
# 工具 2：溯源分析（含绘图）
# ============================================================
@mcp.tool(name="analyze-source", description=ANALYZE_SOURCE_DESCRIPTION)
def analyze_source(
    data: Annotated[
        Optional[Any],
        Field(description="监测数据：固定格式监测文本 / monitoring 结构化对象 / 样本行数组；省略则自动取一批数据"),
    ] = None,
    scenario: Annotated[
        str,
        Field(description=f"仅在 data 省略时生效（自动取数场景）：{SCENARIO_VALUES}"),
    ] = "auto",
    point_count: Annotated[
        int, Field(description="仅在 data 省略时生效：自动生成的点位数量，3~12，默认 6"),
    ] = 6,
    seed: Annotated[Optional[int], Field(description="随机种子；指定后可复现同一批数据")] = None,
    top_k: Annotated[int, Field(description="每条样本返回的前 K 个候选源，默认 3")] = 3,
    plume_events: Annotated[
        int, Field(description="额外生成烟羽扩散地图的事件数，默认 0（不生成）；>0 时写入输出目录"),
    ] = 0,
) -> ToolResult:
    """对监测数据做疑似源溯源分析，并绘制预测源分布图与置信度分布图。"""
    payload = analysis.run_analysis(
        data=data, seed=seed, scenario=scenario, point_count=point_count,
        top_k=top_k, plume_events=plume_events,
    )

    content: list = [mcp_types.TextContent(
        type="text",
        text=payload.get("text_report") or payload.get("error") or "溯源分析未返回结果",
    )]

    for chart in payload.get("artifacts", {}).get("charts", []):
        try:
            content.append(MCPImage(path=chart["path"]))
        except OSError as exc:  # 图片文件异常不应导致整个工具调用失败
            logger.warning("读取统计图失败（%s）: %s", chart.get("path"), exc)

    plume_maps = payload.get("artifacts", {}).get("plume_maps") or []
    if plume_maps:
        content.append(mcp_types.TextContent(
            type="text",
            text="烟羽扩散地图（HTML，服务端文件路径）：\n" + "\n".join(plume_maps),
        ))

    return ToolResult(content=content, structured_content=payload)


# ============================================================
# 自检 / 启动
# ============================================================
def run_self_test() -> int:
    """自检：打印已注册工具、跑一遍取数与溯源（含绘图），不启动任何服务。"""
    tools = asyncio.run(mcp.list_tools())
    print(f"[自检] 已注册 MCP 工具（{len(tools)}）: " + ", ".join(t.name for t in tools))
    print(f"[自检] 监测数据模式 SHIHUA_DATA_MODE = {realtime_data.resolve_mode()}")

    envelope = get_monitoring_data(seed=42, scenario="leak")
    readings = (envelope.get("monitoring") or {}).get("气体浓度", [])
    print(f"[自检] get-monitoring-data -> status={envelope['status']} "
          f"data_source={envelope['data_source']} 点位数={len(readings)}")
    print("-" * 80)
    print(envelope.get("text") or envelope.get("error"))
    print("-" * 80)

    payload = analyze_source(data=envelope.get("text"), seed=42, plume_events=2)
    summary = payload.structured_content
    print(f"[自检] analyze-source -> status={summary['status']} "
          f"样本数={summary['summary'].get('count')} "
          f"图片数={len(summary['artifacts']['charts'])} "
          f"烟羽地图={len(summary['artifacts']['plume_maps'])}")
    print("-" * 80)
    print(payload.content[0].text)
    print("-" * 80)
    print(f"[自检] 产物目录: {summary['artifacts']['run_dir']}")
    return 0


TRANSPORT_ALIASES = {
    "stdio": "stdio",
    "sse": "sse",
    "http": "http",
    "streamable-http": "http",
    "streamable_http": "http",
    "streamablehttp": "http",
}


def normalize_transport(raw: Optional[str]) -> str:
    """把各种写法（含非 ASCII 连字符）统一成 fastmcp 认可的传输名。"""
    key = (raw or "").strip().lower()
    for ch in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212"):
        key = key.replace(ch, "-")
    return TRANSPORT_ALIASES.get(key, key)


def resolve_server_mode(args) -> tuple[str, str, int, str]:
    """决定传输方式，返回 (transport, host, port, path)。

    与 12306-mcp 一致：默认 stdio（平台的 SSE 代理会包住 stdio 子进程）；
    显式给了 --host / --port，或 MCP_TRANSPORT 指定 http / sse 时，进程自己监听端口。
    """
    transport = normalize_transport(args.transport or os.getenv("MCP_TRANSPORT"))
    if args.http:
        transport = "http"
    if args.sse:
        transport = "sse"
    if transport not in ("http", "sse"):
        transport = "http" if (args.host or args.port) else "stdio"

    port = DEFAULT_HTTP_PORT
    for raw in (args.port, os.getenv("PORT"), os.getenv("MCP_PORT")):
        if raw:
            try:
                port = int(raw)
                break
            except (TypeError, ValueError):
                logger.warning("忽略非法的端口值: %r", raw)
    host = args.host or os.getenv("HOST") or os.getenv("MCP_HOST") or DEFAULT_HTTP_HOST
    path = os.getenv("MCP_HTTP_PATH", DEFAULT_HTTP_PATH) or DEFAULT_HTTP_PATH
    return transport, host, port, path


def cors_middleware():
    """HTTP 模式下的 CORS 中间件：避免跨源调用被浏览器 / 网关拦掉。"""
    try:
        from starlette.middleware import Middleware
        from starlette.middleware.cors import CORSMiddleware
    except ImportError as exc:  # pragma: no cover - fastmcp 已依赖 starlette
        logger.warning("CORS 中间件不可用（%s），跨源调用可能被拦截", exc)
        return None

    origins = [o.strip() for o in os.getenv("MCP_CORS_ORIGINS", "*").split(",") if o.strip()]
    return [
        Middleware(
            CORSMiddleware,
            allow_origins=origins,      # ⚠️ 公网部署请改成具体域名，不要用 *
            allow_credentials=False,    # 通配来源必须关闭凭证，否则浏览器直接拒绝响应
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],
        )
    ]


def parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="shihua-mcp",
        description="石化疑似源识别 MCP 服务：数据生成 + 溯源分析（含绘图）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--self-test", action="store_true",
                        help="自检：列出工具并跑一遍取数 + 溯源，不启动服务")
    parser.add_argument("--transport", default=None,
                        help="传输方式：stdio（默认）/ http / sse；也可用 MCP_TRANSPORT 指定")
    parser.add_argument("--http", action="store_true", help="等价于 --transport http")
    parser.add_argument("--sse", action="store_true", help="等价于 --transport sse")
    parser.add_argument("--host", default=None,
                        help=f"HTTP/SSE 监听地址（默认 {DEFAULT_HTTP_HOST}，或取 HOST 环境变量）")
    parser.add_argument("--port", default=None,
                        help=f"HTTP/SSE 监听端口（默认 {DEFAULT_HTTP_PORT}，或取 PORT 环境变量）")
    return parser.parse_args(argv)


def main(argv: Optional[list] = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        return run_self_test()

    transport, host, port, path = resolve_server_mode(args)
    data_mode = realtime_data.resolve_mode()
    if data_mode in ("simulate", "auto"):
        logger.warning(
            "监测数据模式 = %s：未接入现场数据源时使用本地仿真数据，输出的数据来源"
            "恒为 SIMULATED，仿真数据严禁用于现场处置决策。接入现场数据请设置 %s 并置 %s=real。",
            data_mode, realtime_data.ENDPOINT_ENV, realtime_data.MODE_ENV,
        )
    else:
        logger.info("监测数据模式 = %s", data_mode)

    if transport in ("http", "sse"):
        endpoint = path if transport == "http" else "/sse"
        logger.info("MCP 传输模式 = %s，端点 http://%s:%s%s", transport, host, port, endpoint)
        if transport == "sse":
            mcp.run(transport="sse", host=host, port=port)
        else:
            mcp.run(transport="http", host=host, port=port, path=path, middleware=cors_middleware())
    else:
        logger.info("MCP 传输模式 = stdio（工具：get-monitoring-data, analyze-source）")
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
