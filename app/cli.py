# -*- coding: utf-8 -*-
"""
石化疑似源识别 —— 纯命令行版
================================
自动从 data/ 目录随机选取一个或多个测试文本文件并抽取样本行，
完成批量预测、结果汇总、图表绘制与烟羽扩散可视化，
全程无需 Qt 界面，所有结果输出到终端与 output/ 目录。

用法示例:
    python app/cli.py
    python app/cli.py --files 2 --max-lines 12 --seed 42
    python app/cli.py --data-dir D:/proj/data --models-dir D:/proj/models --open-browser
    python app/cli.py --inputs data/测试文本2025-10-10.txt --max-lines 0
"""
import os
import re
import sys
import glob
import random
import argparse
import contextlib
import webbrowser
from datetime import datetime

import matplotlib
matplotlib.use('Agg')  # 无界面后端
import matplotlib.pyplot as plt

# Matplotlib 中文字体设置（与原 Qt 版本保持一致）
plt.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'WenQuanYi Zen Hei']
plt.rcParams['axes.unicode_minus'] = False

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

import paths
from model_design import CharTokenizer, VOCTransformer, CorpusAnalyzer
from plume_visualization import create_plume_visualization

# 原 GUI 配色常量（用于终端与图表一致性）
SECONDARY_COLOR = "#3498db"

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# ============================================================
# 1. 模型加载（复刻原 V8 load_model，不使用 Qt）
# ============================================================
class Predictor:
    """轻量预测器：复用原 SinglePredictor.predict 的推理逻辑。"""

    def __init__(self, model, tokenizer, source2idx, idx2source):
        self.model = model
        self.tokenizer = tokenizer
        self.source2idx = source2idx
        self.idx2source = idx2source

    def predict(self, text: str) -> dict:
        """返回 status/input_text/predicted_source/confidence/top3_results。"""
        try:
            device = next(self.model.parameters()).device
            encoded = self.tokenizer.encode(text).unsqueeze(0).to(device)
            with torch.no_grad():
                outputs = self.model(encoded)
                probs = F.softmax(outputs, dim=1)
                pred_idx = outputs.argmax(dim=1).item()
                confidence = probs[0][pred_idx].item()
                predicted_source = self.idx2source[pred_idx]

                topk = torch.topk(probs, k=min(3, len(self.source2idx)), dim=1)
                top3_results = [
                    (self.idx2source[idx.item()], prob.item())
                    for prob, idx in zip(topk.values[0], topk.indices[0])
                ]
                return {
                    'status': 'success',
                    'input_text': text,
                    'predicted_source': predicted_source,
                    'confidence': confidence,
                    'top3_results': top3_results,
                }
        except Exception as e:
            return {
                'status': 'error',
                'input_text': text,
                'error_message': str(e),
            }


def _infer_model_params(state_dict):
    """从旧格式 model_state_dict 推断模型结构参数（兼容历史模型）。"""
    embedding = state_dict['embedding.weight']
    fc = state_dict['fc.weight']
    vocab_size, d_model = embedding.shape[0], embedding.shape[1]
    n_classes = fc.shape[0]
    layer_idx = []
    for k in state_dict:
        m = re.search(r'transformer\.layers\.(\d+)\.', k)
        if m:
            layer_idx.append(int(m.group(1)))
    num_layers = (max(layer_idx) + 1) if layer_idx else 3
    nhead = 8  # 历史模型固定使用 8 头注意力
    return vocab_size, n_classes, d_model, nhead, num_layers


def load_model(model_path):
    """加载模型，兼容新旧两种 checkpoint 格式。

    新格式（原 V8 训练保存）：model_state_dict / tokenizer_state / label_mapping / model_args
    旧格式（历史版本保存）：  model_state_dict / tokenizer(实例) / source2idx
    """
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"未找到模型文件: {model_path}")

    checkpoint = torch.load(model_path, map_location='cpu', weights_only=False)
    print(f"[状态] checkpoint 键: {list(checkpoint.keys())}")
    sd = checkpoint['model_state_dict']

    if 'tokenizer_state' in checkpoint and 'model_args' in checkpoint and 'label_mapping' in checkpoint:
        # ---------- 新格式 ----------
        tokenizer = CharTokenizer()
        tokenizer.char2idx = checkpoint['tokenizer_state']['char2idx']
        tokenizer.idx2char = checkpoint['tokenizer_state']['idx2char']
        tokenizer.num_chars = checkpoint['tokenizer_state']['num_chars']

        args = checkpoint['model_args']
        model = VOCTransformer(
            vocab_size=args['vocab_size'],
            n_classes=args['n_classes'],
            d_model=args['d_model'],
            nhead=args['nhead'],
            num_layers=args['num_layers'],
        )
        model.load_state_dict(sd)
        source2idx = checkpoint['label_mapping']['source2idx']
        idx2source = checkpoint['label_mapping']['idx2source']

    elif 'tokenizer' in checkpoint and 'source2idx' in checkpoint:
        # ---------- 旧格式（历史脚本训练，含 __main__ 序列化的 tokenizer 实例） ----------
        tokenizer = checkpoint['tokenizer']
        vocab_size, n_classes, d_model, nhead, num_layers = _infer_model_params(sd)
        try:
            model = VOCTransformer(
                vocab_size=vocab_size, n_classes=n_classes, d_model=d_model,
                nhead=nhead, num_layers=num_layers,
            )
            model.load_state_dict(sd)
        except RuntimeError:
            # 极少数历史模型的 pos_encoder 为 Parameter（model_components 结构），做兼容尝试
            try:
                from model_components import VOCTransformer as LegacyVOC
            except ImportError:
                raise RuntimeError("模型结构与 model_design.VOCTransformer 不匹配，且缺少 model_components.py 兜底模块")
            model = LegacyVOC(vocab_size=vocab_size, n_classes=n_classes, d_model=d_model)
            model.load_state_dict(sd)
        source2idx = checkpoint['source2idx']
        idx2source = {v: k for k, v in source2idx.items()}

    else:
        raise KeyError(f"无法识别的 checkpoint 格式，键: {list(checkpoint.keys())}")

    model.eval()
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model.to(device)

    print(f"[状态] 模型加载成功: {model_path}")
    print(f"[状态] 标签类别数: {len(source2idx)}，运行设备: {device}")
    return Predictor(model, tokenizer, source2idx, idx2source)


def preprocess_prediction_text(text: str) -> str:
    """预处理预测文本：只提取传感器和位置信息（与原程序一致）。"""
    parts = text.split('，')
    sensor_part = next((p for p in parts if '传感器' in p), '')
    location_part = next((p for p in parts if '位置为' in p), '')
    if sensor_part and location_part:
        return f"{sensor_part}，{location_part}"
    return text


# ============================================================
# 2. 从文本提取信息（坐标 / 风速风向 / 真实标签）
# ============================================================
def extract_coordinates_from_text(text: str):
    """从文本中提取坐标，支持中文括号（与语料格式一致）。"""
    try:
        if '（' in text and '）' in text:
            coords = text[text.find('（') + 1:text.find('）')]
            longitude, latitude = map(str.strip, coords.split(','))
            return longitude, latitude
    except Exception:
        pass
    return "117.0580", "30.5255"


def extract_location_from_corpus(corpus_analyzer, source_name):
    """从语料库中提取疑似源坐标；语料缺失或找不到时返回默认坐标（与原程序一致）。"""
    if corpus_analyzer is None:
        return "117.02127280", "30.53173852"
    try:
        weather_records = corpus_analyzer.get_source_weather_info(source_name)
        if weather_records:
            return extract_coordinates_from_text(weather_records[0]['full_text'])
    except Exception:
        # 单条解析失败不刷屏，统一使用默认坐标
        pass
    return "117.02127280", "30.53173852"


def extract_weather_from_input_text(text: str):
    """从输入文本提取风速(级)与风向（与原程序一致）。"""
    wind_speed = "3"
    wind_direction = "东风"
    try:
        speed_match = re.search(r'风速为\s*([\d\-]+)\s*级', text)
        if speed_match:
            wind_speed = speed_match.group(1)

        direction_match = re.search(r'风向为\s*([东南西北东北东南西南西北]+风)', text)
        if direction_match:
            wind_direction = direction_match.group(1)
        else:
            if '风向为' in text:
                direction_text = text[text.find('风向为') + 3:]
                end_positions = []
                for end_char in ['，', '。', ' ', '\n']:
                    pos = direction_text.find(end_char)
                    if pos != -1:
                        end_positions.append(pos)
                if end_positions:
                    direction_text = direction_text[:min(end_positions)].strip()
                wind_direction = direction_text.strip()
                if not wind_direction.endswith('风'):
                    wind_direction += '风'
    except Exception as e:
        print(f"[警告] 提取风速风向失败: {e}")
    return wind_speed, wind_direction


def extract_true_label(text: str):
    """若文本含'疑似源为'，返回其中的真实标签（仅用于对照显示）。"""
    if '疑似源为' in text:
        return text.split('疑似源为')[1].strip()
    return None

# ============================================================
# 3. 样本采集（自动随机选取 data/ 下的文本文件）
# ============================================================
def discover_sample_files(data_dir):
    """返回 data 目录下候选 txt 文件（按可复现顺序排序）。"""
    patterns = [os.path.join(data_dir, '*.txt')]
    files = []
    for pat in patterns:
        files.extend(glob.glob(pat))
    return sorted(files)


def is_valid_sample_line(line: str) -> bool:
    return ('传感器' in line) and ('位置为' in line)


def read_sample_lines(file_path, max_lines=None, rng=None):
    """读取文件中的有效样本行；max_lines=None 表示随机抽取 5~20 行。"""
    with open(file_path, 'r', encoding='utf-8', errors='replace') as fp:
        lines = [ln.strip() for ln in fp if ln.strip()]
    valid = [ln for ln in lines if is_valid_sample_line(ln)]
    if not valid:
        return []
    if max_lines is None:
        # 有效行足够时随机取 5~20 行；行数不足时取 1~实际行数，避免 randint 空区间
        upper = min(20, len(valid))
        lower = min(5, upper)
        n = rng.randint(lower, upper)
    elif max_lines <= 0:
        n = len(valid)
    else:
        n = min(max_lines, len(valid))
    # 保持文件内原始顺序，随机抽取 n 行
    indices = sorted(rng.sample(range(len(valid)), n))
    return [valid[i] for i in indices]


def choose_sample_files(data_dir, inputs=None, files=None, rng=None):
    """--inputs 优先；否则随机选 files 个文件（默认 1~3）。"""
    all_files = discover_sample_files(data_dir)
    if not all_files:
        raise FileNotFoundError(f"data 目录下未找到 *.txt 样本: {data_dir}")

    if inputs:
        return [os.path.abspath(p) for p in inputs if os.path.exists(p)]

    if files is None:
        n = rng.randint(1, min(3, len(all_files)))
    else:
        n = min(files, len(all_files))
    chosen = rng.sample(all_files, n)
    return chosen


def clean_source_name(source: str) -> str:
    """图表统计时去除源名称末尾坐标（与原 GUI 正则行为一致）。"""
    cleaned = re.sub(
        r'\s*[（(]\s*[+-]?\d{1,3}(?:\.\d+)?\s*[,，]\s*'
        r'[+-]?\d{1,3}(?:\.\d+)?\s*[）)]?\s*$',
        '', source.strip()
    ).strip()
    return cleaned

# ============================================================
# 4. 结果保存与展示（保留原 GUI 表格/图表/事件信息）
# ============================================================
def build_result_rows(records):
    """records: 每项含 input_text/result/lon/lat/wind/true_label/source_file。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for rec in records:
        res = rec['result']
        input_text = rec['input_text']
        if res['status'] == 'error':
            rows.append({
                '预测时间': now_str,
                '传感器信息': input_text,
                '预测疑似源': '预测错误',
                '疑似源分析': '',
                '经度': rec['longitude'],
                '纬度': rec['latitude'],
                '风速(级)': rec['wind_speed'],
                '风向': rec['wind_direction'],
                '置信度': 'N/A',
                '原始记录': res.get('error_message', ''),
            })
            continue

        predicted_source = res['predicted_source']
        confidence = res['confidence']
        longitude, latitude = rec['longitude'], rec['latitude']
        source_analysis = f"{input_text}，疑似源为{predicted_source}({longitude},{latitude})"
        rows.append({
            '预测时间': now_str,
            '传感器信息': input_text,
            '预测疑似源': predicted_source,
            '疑似源分析': source_analysis,
            '经度': longitude,
            '纬度': latitude,
            '风速(级)': rec['wind_speed'],
            '风向': rec['wind_direction'],
            '置信度': f"{confidence:.4f}",
            '原始记录': source_analysis,
        })
    return rows


def print_result_records(records):
    """终端逐条输出（对应原 GUI 表格每一行 + 置信度背景色分级提示）。"""
    print("\n" + "=" * 90)
    print("批量预测结果明细")
    print("=" * 90)
    for i, rec in enumerate(records, 1):
        res = rec['result']
        origin = os.path.basename(rec['source_file'])
        print(f"\n[{i}] 来源文件: {origin}")
        print(f"    传感器信息: {rec['input_text']}")
        if res['status'] == 'error':
            print(f"    预测疑似源: 预测错误")
            print(f"    错误信息: {res.get('error_message', '')}")
            continue
        conf = res['confidence']
        if conf > 0.8:
            level = "高置信度"
        elif conf > 0.5:
            level = "中置信度"
        else:
            level = "低置信度"
        print(f"    预测疑似源: {res['predicted_source']}  | 置信度: {conf:.4f}  ({level})")
        print(f"    预测Top-3:")
        for rank, (src, prob) in enumerate(res['top3_results'], 1):
            print(f"      {rank}. {src}: {prob:.4f}")
        if rec['true_label']:
            mark = "一致" if clean_source_name(rec['true_label']) == clean_source_name(res['predicted_source']) else "不一致"
            print(f"    真实标签(对照): {rec['true_label']}  [{mark}]")
        print(f"    经度: {rec['longitude']}  纬度: {rec['latitude']}  "
              f"风速(级): {rec['wind_speed']}  风向: {rec['wind_direction']}")


def print_events_and_summary(records, df_rows):
    """事件下拉信息 + 源分布 + 置信度汇总（对应 GUI 事件框与统计信息）。"""
    print("\n" + "=" * 90)
    print("事件列表（对应原界面'选择事件'下拉框）")
    print("=" * 90)
    for i, rec in enumerate(records, 1):
        src = rec['result'].get('predicted_source', '预测错误') if rec['result']['status'] == 'success' else '预测错误'
        print(f"  事件{i}: {src}")

    success = [r for r in records if r['result']['status'] == 'success']
    print("\n" + "=" * 90)
    print("预测源分布（全部类别，Top-12 见柱状图）")
    print("=" * 90)
    if success:
        counter = {}
        for rec in success:
            name = clean_source_name(rec['result']['predicted_source'])
            counter[name] = counter.get(name, 0) + 1
        for name, cnt in sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:12]:
            print(f"  {name}: {cnt}")
        total = sum(counter.values())
        print(f"  合计: {total} 条有效预测")

    print("\n" + "=" * 90)
    print("置信度统计")
    print("=" * 90)
    confs = [r['result']['confidence'] for r in success]
    if confs:
        print(f"  样本数: {len(confs)}")
        print(f"  均值: {np.mean(confs):.4f}  最小值: {min(confs):.4f}  最大值: {max(confs):.4f}")
        high = sum(1 for c in confs if c > 0.8)
        mid = sum(1 for c in confs if 0.5 < c <= 0.8)
        low = sum(1 for c in confs if c <= 0.5)
        print(f"  高置信度(>0.8): {high}  中置信度(0.5~0.8): {mid}  低置信度(<=0.5): {low}")


def save_excel(records, output_excel):
    rows = build_result_rows(records)
    df = pd.DataFrame(rows)
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("[警告] 未安装 openpyxl，已跳过 Excel 导出。可执行: pip install openpyxl")
        return None
    os.makedirs(os.path.dirname(output_excel), exist_ok=True)
    with pd.ExcelWriter(output_excel, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='预测结果')
    print(f"\n[输出] 预测结果已保存到 Excel: {output_excel}")
    return df

# ============================================================
# 5. 图表绘制（对应原 GUI 的"预测源分布 / 置信度分布"）
# ============================================================
def plot_source_distribution(records, output_path):
    """柱状图：预测源分布 Top-12（复刻原 update_chart）。"""
    counter = {}
    for rec in records:
        res = rec['result']
        if res['status'] != 'success':
            continue
        name = clean_source_name(res['predicted_source'])
        counter[name] = counter.get(name, 0) + 1
    if not counter:
        return

    top_sources = sorted(counter.items(), key=lambda kv: kv[1], reverse=True)[:12]
    names = [n for n, _ in top_sources]
    values = [v for _, v in top_sources]

    fig, ax = plt.subplots(figsize=(11, 6))
    bars = ax.bar(names, values, color=SECONDARY_COLOR)
    ax.set_title('预测源分布（Top 12）')
    ax.set_xlabel('预测源')
    ax.set_ylabel('数量')
    ax.tick_params(axis='x', labelrotation=30, labelsize=9)
    for label in ax.get_xticklabels():
        label.set_horizontalalignment('right')
    ax.bar_label(bars, padding=3, fontsize=9)
    ax.set_ylim(0, max(values) * 1.12)
    ax.grid(axis='y', linestyle='--', alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"[输出] 预测源分布图已保存: {output_path}")


def plot_confidence_distribution(records, output_path):
    """直方图：置信度分布 bins=10（复刻原 update_chart）。"""
    confs = [r['result']['confidence'] for r in records
             if r['result']['status'] == 'success']
    if not confs:
        return
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.hist(confs, bins=10, range=(0, 1), edgecolor='black')
    ax.set_title('置信度分布')
    ax.set_xlabel('置信度')
    ax.set_ylabel('数量')
    ax.set_xlim(0, 1)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"[输出] 置信度分布图已保存: {output_path}")


# ============================================================
# 6. 烟羽扩散可视化（复用原 plume_visualization 模块）
# ============================================================
def generate_plume_htmls(kml_path, excel_path, plume_dir, max_events=None):
    """对预测结果逐事件生成烟羽扩散 HTML，并写一个总览 index.html。"""
    if not os.path.exists(kml_path):
        print(f"[警告] KML 文件不存在，跳过烟羽可视化: {kml_path}")
        return []

    df = pd.read_excel(excel_path, engine='openpyxl')
    if df.empty:
        print("[警告] 预测结果为空，跳过烟羽可视化")
        return []

    os.makedirs(plume_dir, exist_ok=True)
    total = len(df)
    if max_events is None or max_events <= 0:
        n = total
    else:
        n = min(max_events, total)

    produced = []
    for idx in range(n):
        out_html = os.path.join(plume_dir, f"event_{idx + 1:03d}.html")
        print(f"[状态] 正在生成烟羽可视化: 事件 {idx + 1}/{n} ...")
        try:
            # 屏蔽模块内部的逐行调试输出，异常时保留报错
            with contextlib.redirect_stdout(open(os.devnull, 'w', encoding='utf-8')):
                map_obj = create_plume_visualization(
                    kml_path=kml_path,
                    excel_path=excel_path,
                    output_html_path=out_html,
                    current_column_index=idx,
                )
            if map_obj and os.path.exists(out_html):
                produced.append(out_html)
                print(f"[输出] 事件{idx + 1} 烟羽可视化: {out_html}")
            else:
                print(f"[警告] 事件{idx + 1} 烟羽可视化生成失败")
        except Exception as e:
            print(f"[警告] 事件{idx + 1} 烟羽可视化异常: {e}")

    # 总览索引页（对应原"选择事件"下拉，可点开各事件地图）
    if produced:
        index_path = os.path.join(plume_dir, "index.html")
        cards = []
        for i, html in enumerate(produced, 1):
            cards.append(
                f'<li><a href="{os.path.basename(html)}">事件{i}: 查看烟羽扩散地图</a></li>'
            )
        html_body = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>烟羽扩散可视化 - 事件总览</title></head><body style="font-family:Microsoft YaHei;">
<h2>烟羽扩散可视化事件总览</h2>
<p>共 {len(produced)} 个事件，点击查看对应烟羽扩散地图（地图瓦片需联网加载）。</p>
<ol>{''.join(cards)}</ol></body></html>"""
        with open(index_path, 'w', encoding='utf-8') as fp:
            fp.write(html_body)
        produced.append(index_path)
        print(f"[输出] 烟羽可视化总览页: {index_path}")
    return produced

# ============================================================
# 7. 命令行入口
# ============================================================
def parse_args():
    parser = argparse.ArgumentParser(
        description="石化疑似源识别 CLI：自动采样 data/ 文本并完成预测、统计、烟羽可视化。")
    parser.add_argument('--data-dir', default=paths.DATA_DIR,
                        help='样本/语料/KML 目录（默认 data/，可用 SHIHUA_DATA_DIR 覆盖）')
    parser.add_argument('--models-dir', default=paths.MODELS_DIR,
                        help='模型目录（默认 models/）')
    parser.add_argument('--output-dir', default=paths.OUTPUT_DIR,
                        help='输出目录（默认 output/）')
    parser.add_argument('--inputs', nargs='*', default=None,
                        help='指定一个或多个样本文件；不指定则自动随机选取')
    parser.add_argument('--files', type=int, default=None,
                        help='随机选取的文件数（默认 1~3）')
    parser.add_argument('--max-lines', type=int, default=None,
                        help='每个文件随机抽取行数（默认 5~20，<=0 表示整文件）')
    parser.add_argument('--plume-events', type=int, default=5,
                        help='生成烟羽可视化的前 N 个事件（<=0 表示全部）')
    parser.add_argument('--seed', type=int, default=None,
                        help='随机种子（便于复现，不指定则每次随机）')
    parser.add_argument('--open-browser', action='store_true',
                        help='生成后自动打开浏览器查看烟羽可视化总览')
    parser.add_argument('--no-plume', action='store_true',
                        help='跳过烟羽可视化生成')
    return parser.parse_args()


def main():
    args = parse_args()
    rng = random.Random(args.seed)

    data_dir = os.path.abspath(args.data_dir)
    models_dir = os.path.abspath(args.models_dir)
    out_root = os.path.abspath(args.output_dir)
    os.makedirs(out_root, exist_ok=True)

    if not os.path.isdir(data_dir):
        print(f"[错误] data 目录不存在: {data_dir}")
        sys.exit(1)

    # ---- 随机选取文件与样本行 ----
    files = choose_sample_files(data_dir, inputs=args.inputs, files=args.files, rng=rng)
    print("\n[状态] 本次随机选中的样本文件：")
    all_records_inputs = []  # (file, line)
    for fp in files:
        lines = read_sample_lines(fp, max_lines=args.max_lines, rng=rng)
        for ln in lines:
            all_records_inputs.append((fp, ln))
        print(f"  - {os.path.basename(fp)}  (抽取 {len(lines)} 行)")

    if not all_records_inputs:
        print("[错误] 所选文件中没有找到包含'传感器'和'位置为'的有效样本行")
        sys.exit(1)

    # ---- 语料库（坐标/天气知识库，缺失时坐标用默认值）----
    corpus_path = os.path.join(data_dir, '语料库带经维度2020-2025.txt')
    corpus_analyzer = None
    if os.path.exists(corpus_path):
        try:
            corpus_analyzer = CorpusAnalyzer(corpus_file=corpus_path)
            print(f"[状态] 语料库加载成功: {corpus_path}")
        except Exception as e:
            print(f"[警告] 语料库加载失败，坐标将使用默认值: {e}")
    else:
        print(f"[警告] 语料库不存在，坐标将使用默认值: {corpus_path}")

    # ---- 加载模型 ----
    retrained = os.path.join(models_dir, 'voc_model_retrained.pth')
    base_model = os.path.join(models_dir, 'voc_model.pth')
    model_path = retrained if os.path.exists(retrained) else base_model
    predictor = load_model(model_path)

    # ---- 批量预测 ----
    print(f"\n[状态] 开始批量预测，共 {len(all_records_inputs)} 条样本 ...")
    records = []
    for file_path, original_text in all_records_inputs:
        processed = preprocess_prediction_text(original_text)
        result = predictor.predict(processed)
        wind_speed, wind_direction = extract_weather_from_input_text(original_text)

        longitude, latitude = "117.02127280", "30.53173852"
        if result['status'] == 'success':
            longitude, latitude = extract_location_from_corpus(corpus_analyzer, result['predicted_source'])

        records.append({
            'source_file': file_path,
            'input_text': original_text,
            'processed_text': processed,
            'result': result,
            'longitude': longitude,
            'latitude': latitude,
            'wind_speed': wind_speed,
            'wind_direction': wind_direction,
            'true_label': extract_true_label(original_text),
        })

    print_result_records(records)

    # ---- 保存 Excel ----
    run_dir = os.path.join(out_root, datetime.now().strftime("run_%Y%m%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)
    excel_path = os.path.join(run_dir, '预测结果.xlsx')
    df_results = save_excel(records, excel_path)

    print_events_and_summary(records, None)

    # ---- 图表 ----
    chart1 = os.path.join(run_dir, '预测源分布_Top12.png')
    chart2 = os.path.join(run_dir, '置信度分布.png')
    plot_source_distribution(records, chart1)
    plot_confidence_distribution(records, chart2)

    # ---- 烟羽可视化 ----
    produced = []
    if not args.no_plume and df_results is not None and os.path.exists(excel_path):
        kml_path = os.path.join(data_dir, '安庆监测点位及分区 202403.kml')
        plume_dir = os.path.join(run_dir, 'plume')
        produced = generate_plume_htmls(kml_path, excel_path, plume_dir, max_events=args.plume_events)
    elif not args.no_plume and df_results is None:
        print("[警告] Excel 未生成，已跳过烟羽扩散可视化（缺少 openpyxl）")

    print("\n" + "=" * 90)
    print("本次运行汇总")
    print("=" * 90)
    print(f"  样本文件数: {len(files)}，预测条数: {len(all_records_inputs)}")
    print(f"  输出目录: {run_dir}")
    print(f"  结果 Excel: {excel_path}" + ("" if df_results is not None else "（已跳过，缺 openpyxl）"))
    print(f"  预测源分布图: {chart1}")
    print(f"  置信度分布图: {chart2}")
    for i, html in enumerate(produced, 1):
        print(f"  可视化 {i}: {html}")

    if args.open_browser and produced:
        target = produced[-1] if os.path.basename(produced[-1]) == 'index.html' else produced[0]
        webbrowser.open('file://' + os.path.abspath(target))
        print(f"[状态] 已用默认浏览器打开: {target}")


if __name__ == '__main__':
    main()
