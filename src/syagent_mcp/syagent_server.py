"""
石化疑似源识别 MCP Server 骨架
建议放置路径：src/syagent_mcp/server.py

依赖新增：uv add "mcp[cli]" fastmcp
依赖原有：torch / pandas / numpy / scikit-learn / scipy / matplotlib / folium / openpyxl
"""
import os
import sys
from pathlib import Path

from fastmcp import FastMCP

# 让 app/ 目录可被导入（按你的实际结构调整）
try:
    from . import cli  # 当 app 作为包内模块时
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import cli  # 仓库根目录在 sys.path 时

mcp = FastMCP("石化疑似源识别（Shihua VOC）")

DATA_DIR = Path(os.getenv("SYAGENT_DATA_DIR", "data"))
MODELS_DIR = Path(os.getenv("SYAGENT_MODELS_DIR", "models"))
OUTPUT_DIR = Path(os.getenv("SYAGENT_OUTPUT_DIR", "output"))

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
    """懒加载模型：首次调用时才加载，避免启动阻塞。"""
    global _PREDICTOR
    if _PREDICTOR is None:
        # 按 cli.load_model 的实际返回值解包（model, tokenizer, source2idx, idx2source）
        model, tokenizer, source2idx, idx2source = cli.load_model(str(_pick_model_path()))
        _PREDICTOR = cli.Predictor(model, tokenizer, source2idx, idx2source)
    return _PREDICTOR


@mcp.tool()
def predict_text(text: str) -> dict:
    """对单条监测文本做疑似源识别。

    输入形如「传感器…，位置为…」的文本，返回疑似源、经纬度、置信度与 Top-3 候选。
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
    """批量抽样预测：抽样 → 推理 → 导出 Excel 与统计图表 → 生成烟羽可视化 HTML。"""
    import random

    rng = random.Random(seed) if seed is not None else random.Random()
    chosen = cli.choose_sample_files(str(DATA_DIR), files=files, rng=rng)
    records = []
    for f in chosen:
        for line in cli.read_sample_lines(f, max_lines=max_lines or None, rng=rng):
            records.append(get_predictor().predict(cli.preprocess_prediction_text(line)))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    excel_path = OUTPUT_DIR / "预测结果.xlsx"
    cli.save_excel(records, str(excel_path))
    cli.plot_source_distribution(records, str(OUTPUT_DIR / "预测源分布_Top12.png"))
    cli.plot_confidence_distribution(records, str(OUTPUT_DIR / "置信度分布.png"))

    kml = next(DATA_DIR.glob("*.kml"), None)
    plume_dir = OUTPUT_DIR / "plume"
    if kml:
        cli.generate_plume_htmls(str(kml), str(excel_path), str(plume_dir), plume_events)

    return {
        "count": len(records),
        "excel": str(excel_path),
        "plume_index": str(plume_dir / "index.html") if kml else None,
    }


def main():
    mcp.run()


if __name__ == "__main__":
    main()
