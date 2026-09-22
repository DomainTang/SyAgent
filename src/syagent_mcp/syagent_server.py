"""
石化疑似源识别 MCP Server 骨架
路径：src/syagent_mcp/server.py
依赖：uv add "mcp[cli]" fastmcp
原有依赖：torch / pandas / numpy / scikit‑learn / scipy / matplotlib / folium / openpyxl
"""
import os
import sys
from pathlib import Path
from fastmcp import FastMCP
from starlette.routing import Route
from starlette.responses import JSONResponse

# matplotlib容器无GUI，强制非交互式后端
os.environ["MPLBACKEND"] = "Agg"

# 让 app/ 目录可被导入
try:
    from . import cli
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import cli

mcp = FastMCP("石化疑似源识别（Shihua VOC）", stateless_http=True)

# ======================新增魔搭健康检查接口======================
async def health_endpoint(request):
    """魔搭容器健康探测 /health，返回200 OK"""
    return JSONResponse({"status": "ok", "service": "syagent-mcp"}, status_code=200)

# 将健康路由注入FastMCP底层starlette app
if hasattr(mcp, "_app"):
    mcp._app.routes.append(Route("/health", health_endpoint, methods=["GET"]))
# ==============================================================

DATA_DIR = Path(os.getenv("SYAGENT_DATA_DIR", "data"))
MODELS_DIR = Path(os.getenv("SYAGENT_MODELS_DIR", "models"))
OUTPUT_DIR = Path(os.getenv("SYAGENT_OUTPUT_DIR", "/tmp/syagent_output"))
_PREDICTOR = None


def _pick_model_path() -> Path:
    """优先 retrained 权重，其次 voc_model.pth。"""
    for name in ("voc_model_retrained.pth", "voc_model.pth"):
        p = MODELS_DIR / name
        if p.exists():
            return p
    raise FileNotFoundError(
        f"未在 {MODELS_DIR} 下找到 voc_model*.pth，请检查 SYAGENT_MODELS_DIR"
    )


def get_predictor():
    """懒加载模型：首次调用工具才加载模型，**启动阶段不加载模型！！**
    ⚠️非常关键：如果启动main()就加载大torch模型，容器启动时间超时，魔搭健康检查直接失败。
    懒加载：容器启动只拉起http服务，等Dify真正调用predict_text才加载权重。
    """
    global _PREDICTOR
    if _PREDICTOR is None:
        model, tokenizer, source2idx, idx2source = cli.load_model(str(_pick_model_path()))
        _PREDICTOR = cli.Predictor(model, tokenizer, source2idx, idx2source)
    return _PREDICTOR


@mcp.tool()
def predict_text(text: str) -> dict:
    """对单条监测文本做疑似源识别。
    输入形如「传感器…，位置为…」的文本，返回疑似源、经纬度、置信度与 Top‑3 候选。
    """
    predictor = get_predictor()
    result = predictor.predict(cli.preprocess_prediction_text(text))
    return {
        "source": result.get("source") or result.get("predicted_source"),
        "lon": result.get("lon"),
        "lat": result.get("lat"),
        "confidence": result.get("confidence"),
        "top3": result.get("top3", []),
    }


@mcp.tool()
def list_sample_files() -> list:
    """列出样本目录下可用的样本文件名。"""
    return [str(p) for p in cli.discover_sample_files(str(DATA_DIR))]


@mcp.tool()
def batch_predict(
    files: int = 2,
    max_lines: int = 12,
    plume_events: int = 3,
    seed: int | None = None,
) -> dict:
    """批量抽样预测：抽样 → 推理 → 返回结构化统计结果。
    ⚠️魔搭托管容器为临时实例，不再返回本地磁盘excel/png/html文件路径，只返回内存内统计数据。
    """
    import random
    rng = random.Random(seed) if seed is not None else random.Random()
    chosen = cli.choose_sample_files(str(DATA_DIR), files=files, rng=rng)
    records = []
    for f in chosen:
        for line in cli.read_sample_lines(f, max_lines=max_lines or None, rng=rng):
            rec = get_predictor().predict(cli.preprocess_prediction_text(line))
            records.append(rec)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    source_counter = {}
    conf_sum = 0.0
    for item in records:
        src = item.get("source", "unknown")
        source_counter[src] = source_counter.get(src, 0) + 1
        conf = item.get("confidence", 0.0)
        if isinstance(conf, float):
            conf_sum += conf
    avg_confidence = conf_sum / len(records) if records else 0.0

    return {
        "count": len(records),
        "avg_confidence": round(avg_confidence,4),
        "source_distribution": source_counter,
        "predict_records": records,
        "note": "魔搭托管容器为临时环境，Excel/图片/HTML不会持久保存；如需文件请本地Stdio模式运行导出。"
    }


def main():
    """
    通过环境变量 MCP_TRANSPORT 切换模式：
    - stdio：本地客户端(Cherry‑Studio/Cursor)使用；
    - streamable‑http：魔搭可托管部署，Dify调用；
    """
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    port = int(os.getenv("MCP_PORT", "8000"))
    host = "0.0.0.0"

    if transport == "streamable‑http":
        mcp.run(transport="streamable‑http", host=host, port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
