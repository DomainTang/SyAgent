# -*- coding: utf-8 -*-
"""模型加载与单条推理（自原 CLI 抽出，供 MCP 工具、自检与测试复用）。

兼容两类 checkpoint：
- 新格式：``model_state_dict`` / ``tokenizer_state`` / ``label_mapping`` / ``model_args``
- 旧格式：``model_state_dict`` / ``tokenizer``(实例) / ``source2idx``

注意：本模块只往 stderr（logging）写日志，绝不往 stdout 写内容——
MCP 走 stdio 传输时，stdout 是 JSON-RPC 通道，写入任何字符都会打断协议。
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Optional, Tuple

import torch
import torch.nn.functional as F

from . import paths
from .model_design import CharTokenizer, VOCTransformer

logger = logging.getLogger(__name__)


class Predictor:
    """轻量预测器：复用原 SinglePredictor.predict 的推理逻辑。"""

    def __init__(self, model, tokenizer, source2idx, idx2source):
        self.model = model
        self.tokenizer = tokenizer
        self.source2idx = source2idx
        self.idx2source = idx2source

    @property
    def labels(self) -> list[str]:
        """全部疑似源标签（用于检查 Top-K 的 K 值上限）。"""
        return list(self.source2idx)

    def predict(self, text: str, top_k: int = 3) -> dict:
        """返回 status / input_text / predicted_source / confidence / top3_results。"""
        try:
            device = next(self.model.parameters()).device
            encoded = self.tokenizer.encode(text).unsqueeze(0).to(device)
            with torch.no_grad():
                outputs = self.model(encoded)
                probs = F.softmax(outputs, dim=1)
                pred_idx = outputs.argmax(dim=1).item()
                confidence = probs[0][pred_idx].item()
                predicted_source = self.idx2source[pred_idx]

                k = max(1, min(int(top_k), len(self.source2idx)))
                topk = torch.topk(probs, k=k, dim=1)
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
        except Exception as e:  # noqa: BLE001 - 单条失败不应中断整批分析
            logger.warning("推理失败: %s", e)
            return {
                'status': 'error',
                'input_text': text,
                'error_message': str(e),
            }


def _infer_model_params(state_dict) -> Tuple[int, int, int, int, int]:
    """从旧格式 model_state_dict 推断模型结构参数（兼容历史模型）。"""
    embedding = state_dict['embedding.weight']
    fc = state_dict['fc.weight']
    vocab_size, d_model = embedding.shape[0], embedding.shape[1]
    n_classes = fc.shape[0]
    layer_idx = []
    for key in state_dict:
        m = re.search(r'transformer\.layers\.(\d+)\.', key)
        if m:
            layer_idx.append(int(m.group(1)))
    num_layers = (max(layer_idx) + 1) if layer_idx else 3
    nhead = 8  # 历史模型固定使用 8 头注意力
    return vocab_size, n_classes, d_model, nhead, num_layers


def load_predictor(model_path: str | None = None) -> Predictor:
    """加载模型权重并返回 Predictor。"""
    path = str(model_path) if model_path else str(paths.find_model_path())
    checkpoint = torch.load(path, map_location='cpu', weights_only=False)
    logger.info("checkpoint 键: %s", list(checkpoint.keys()))
    state_dict = checkpoint['model_state_dict']

    if 'tokenizer_state' in checkpoint and 'model_args' in checkpoint and 'label_mapping' in checkpoint:
        # ---------- 新格式（原 V8 界面训练产物） ----------
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
        model.load_state_dict(state_dict)
        source2idx = checkpoint['label_mapping']['source2idx']
        idx2source = checkpoint['label_mapping']['idx2source']

    elif 'tokenizer' in checkpoint and 'source2idx' in checkpoint:
        # ---------- 旧格式（历史脚本训练，含序列化的 tokenizer 实例） ----------
        tokenizer = checkpoint['tokenizer']
        vocab_size, n_classes, d_model, nhead, num_layers = _infer_model_params(state_dict)
        model = VOCTransformer(
            vocab_size=vocab_size, n_classes=n_classes, d_model=d_model,
            nhead=nhead, num_layers=num_layers,
        )
        model.load_state_dict(state_dict)
        source2idx = checkpoint['source2idx']
        idx2source = {v: k for k, v in source2idx.items()}

    else:
        raise KeyError(f"无法识别的 checkpoint 格式，键: {list(checkpoint.keys())}")

    model.eval()
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model.to(device)
    logger.info("模型加载成功: %s（类别数 %d，设备 %s）", path, len(source2idx), device)
    return Predictor(model, tokenizer, source2idx, idx2source)


_PREDICTOR: Optional[Predictor] = None


def get_predictor(model_path: str | None = None) -> Predictor:
    """懒加载并缓存模型：首次调用工具时才加载，避免服务启动阶段超时。"""
    global _PREDICTOR
    if _PREDICTOR is None:
        _PREDICTOR = load_predictor(model_path)
    return _PREDICTOR


def preprocess_prediction_text(text: str) -> str:
    """预处理预测文本：只保留传感器与位置信息（与原程序一致）。"""
    parts = text.split('，')
    sensor_part = next((p for p in parts if '传感器' in p), '')
    location_part = next((p for p in parts if '位置为' in p), '')
    if sensor_part and location_part:
        return f"{sensor_part}，{location_part}"
    return text


__all__ = ["Predictor", "load_predictor", "get_predictor", "preprocess_prediction_text"]
