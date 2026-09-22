# -*- coding: utf-8 -*-
"""MCP 服务层自检：工具注册、传输名归一化、两个工具端到端可用（含图片返回）。

运行方式（二选一）:
    python tests/test_mcp_server.py
    pytest tests/test_mcp_server.py
"""
import asyncio
import os
import sys
import tempfile

import mcp.types as mcp_types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from app import paths, realtime_data as rd  # noqa: E402
from shihua_mcp import server as srv  # noqa: E402

EXPECTED_TOOLS = {"get-monitoring-data", "analyze-source"}


# ---------------------------------------------------------------- 注册与传输
def test_only_the_two_documented_tools_are_registered():
    names = {t.name for t in asyncio.run(srv.mcp.list_tools())}
    assert names == EXPECTED_TOOLS, names


def test_transport_aliases_normalized():
    assert srv.normalize_transport("streamable-http") == "http"
    assert srv.normalize_transport("http") == "http"
    assert srv.normalize_transport("stdio") == "stdio"
    assert srv.normalize_transport("sse") == "sse"
    # U+2011 非断行连字符等历史写法
    assert srv.normalize_transport("streamable\u2011http") == "http"
    assert srv.normalize_transport("  Streamable-HTTP  ") == "http"
    assert srv.normalize_transport(None) == ""


def test_server_mode_defaults_to_stdio():
    transport, host, port, path = srv.resolve_server_mode(srv.parse_args([]))
    assert transport == "stdio"
    assert path == "/mcp" and port == 8000


def test_server_mode_http_when_port_or_flag_given():
    transport, host, port, _ = srv.resolve_server_mode(srv.parse_args(["--port", "8080"]))
    assert (transport, port) == ("http", 8080)
    transport, _, _, _ = srv.resolve_server_mode(srv.parse_args(["--sse"]))
    assert transport == "sse"


def test_server_mode_reads_transport_env():
    original = os.environ.get("MCP_TRANSPORT")
    try:
        os.environ["MCP_TRANSPORT"] = "streamable\u2011http"
        transport, _, _, _ = srv.resolve_server_mode(srv.parse_args([]))
        assert transport == "http"
    finally:
        if original is None:
            os.environ.pop("MCP_TRANSPORT", None)
        else:
            os.environ["MCP_TRANSPORT"] = original


# ---------------------------------------------------------------- 工具 1：数据生成
def test_get_monitoring_data_returns_usable_batch():
    env = srv.get_monitoring_data(seed=42)
    assert env["status"] == "ok"
    assert env["data_source"] == rd.DATA_SOURCE_SIMULATED
    assert env["monitoring"]["气体浓度"], "应至少返回一个点位"
    assert env["validation"]["proceed"] is True
    assert "【数据来源】" in env["text"] and "SIMULATED" in env["text"]
    assert env["simulation_id"].startswith("SIM-")
    assert len(env["sample_lines"]) == len(env["monitoring"]["气体浓度"])
    assert "analyze-source" in env["next_step"]


def test_get_monitoring_data_respects_point_count_and_scenario():
    env = srv.get_monitoring_data(seed=1, scenario="leak", point_count=3)
    assert len(env["monitoring"]["气体浓度"]) == 3
    assert env["scenario"] == "leak"


def test_get_monitoring_data_rejects_unknown_scenario():
    env = srv.get_monitoring_data(scenario="nope")
    assert env["status"] == "error" and "未知场景" in env["error"]


# ---------------------------------------------------------------- 工具 2：溯源分析
def test_analyze_source_returns_report_and_charts():
    with tempfile.TemporaryDirectory() as tmp:
        original = paths.OUTPUT_DIR
        paths.OUTPUT_DIR = tmp  # type: ignore[assignment]
        try:
            envelope = srv.get_monitoring_data(seed=42, scenario="leak")
            result = srv.analyze_source(data=envelope["text"], seed=42)
        finally:
            paths.OUTPUT_DIR = original  # type: ignore[assignment]

    assert isinstance(result, mcp_types.CallToolResult) or hasattr(result, "content")
    payload = result.structured_content
    assert payload["status"] == "success"
    assert payload["data_source"] == rd.DATA_SOURCE_SIMULATED
    assert payload["summary"]["count"] == len(envelope["monitoring"]["气体浓度"])
    assert [c["name"] for c in payload["artifacts"]["charts"]] == ["预测源分布_Top12", "置信度分布"]
    assert payload["artifacts"]["plume_maps"] == [], "plume_events 默认 0 时不应生成烟羽地图"
    assert "SIMULATED" in result.content[0].text

    images = [c for c in result.content if getattr(c, "type", None) == "image"]
    assert len(images) == 2, "两张统计图应以图片内容一并返回"
    assert all(c.mime_type == "image/png" and c.data for c in images)


def test_analyze_source_accepts_structured_and_raw_inputs():
    with tempfile.TemporaryDirectory() as tmp:
        original = paths.OUTPUT_DIR
        paths.OUTPUT_DIR = tmp  # type: ignore[assignment]
        try:
            envelope = srv.get_monitoring_data(seed=7, scenario="normal", point_count=4)
            by_monitoring = srv.analyze_source(data=envelope["monitoring"], seed=7)
            assert by_monitoring.structured_content["status"] == "success"
            assert by_monitoring.structured_content["summary"]["count"] == 4

            single = srv.analyze_source(data="传感器141，位置为生产指挥中心，风速为2级，风向为东北风")
            payload = single.structured_content
            assert payload["status"] == "success"
            # 单条样本同样会画「预测源分布 / 置信度分布」两张图
            assert len([c for c in single.content if getattr(c, "type", None) == "image"]) == 2
        finally:
            paths.OUTPUT_DIR = original  # type: ignore[assignment]
    # 用户直接给出的样本行未声明来源，应如实标注为 USER 而不是 SIMULATED
    assert payload["data_source"] == "USER"


def test_analyze_source_accepts_json_round_trip():
    """客户端把上一个工具的结果 JSON 整段回传时，也必须能正确解析出样本。"""
    import json

    with tempfile.TemporaryDirectory() as tmp:
        original = paths.OUTPUT_DIR
        paths.OUTPUT_DIR = tmp  # type: ignore[assignment]
        try:
            envelope = srv.get_monitoring_data(seed=5, scenario="leak", point_count=4)
            result = srv.analyze_source(data=json.dumps(envelope, ensure_ascii=False), seed=5)
        finally:
            paths.OUTPUT_DIR = original  # type: ignore[assignment]
    summary = result.structured_content["summary"]
    assert summary["count"] == 4, summary
    assert result.structured_content["data_source"] == rd.DATA_SOURCE_SIMULATED


def test_analyze_source_can_plume_maps():
    with tempfile.TemporaryDirectory() as tmp:
        original = paths.OUTPUT_DIR
        paths.OUTPUT_DIR = tmp  # type: ignore[assignment]
        try:
            result = srv.analyze_source(seed=3, point_count=3, scenario="leak", plume_events=1)
            plume_maps = result.structured_content["artifacts"]["plume_maps"]
            assert plume_maps, "plume_events>0 时应生成烟羽地图 + 总览页"
            assert all(os.path.exists(path) for path in plume_maps), plume_maps
            assert any(path.endswith("index.html") for path in plume_maps), plume_maps
        finally:
            paths.OUTPUT_DIR = original  # type: ignore[assignment]


def test_mcp_client_round_trip():
    """用 fastmcp 客户端真调一次工具，确认协议层返回文本 + 图片。"""
    from fastmcp import Client

    async def _call():
        async with Client(srv.mcp) as client:
            tools = await client.list_tools()
            data_result = await client.call_tool("get-monitoring-data", {"seed": 42, "point_count": 3})
            text = data_result.content[0].text
            assert "【数据来源】" in text
            analysis_result = await client.call_tool("analyze-source", {"data": text, "seed": 42})
            return {t.name for t in tools}, analysis_result

    with tempfile.TemporaryDirectory() as tmp:
        original = paths.OUTPUT_DIR
        paths.OUTPUT_DIR = tmp  # type: ignore[assignment]
        try:
            names, result = asyncio.run(_call())
        finally:
            paths.OUTPUT_DIR = original  # type: ignore[assignment]

    assert names == EXPECTED_TOOLS
    types = [getattr(c, "type", None) for c in result.content]
    assert types.count("image") >= 1 and "text" in types


def test_data_dirs_are_absolute():
    assert paths.DATA_DIR.is_absolute() and paths.MODELS_DIR.is_absolute()
    assert paths.OUTPUT_DIR.is_absolute()


def _run_all():
    funcs = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in funcs:
        try:
            fn()
            print(f"[PASS] {name}")
        except Exception as exc:  # noqa: BLE001 - 自检脚本，失败即报告
            failed += 1
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(funcs) - failed}/{len(funcs)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
