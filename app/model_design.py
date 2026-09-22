# -*- coding: utf-8 -*-
"""疑似源识别模型结构、字符分词器与语料库解析（仅保留推理链路所需部分）。

与原工程（Qt 版）保持一致的三件事：
1. ``CharTokenizer`` / ``VOCTransformer`` 的结构必须与训练时完全一致，
   否则 ``load_state_dict`` 会直接失败；
2. ``CorpusAnalyzer`` 负责把语料库里的疑似源名称映射到经纬度与风况；
3. 训练相关代码（Dataset / Trainer / 训练入口）已移除——本工程只做推理。
"""
from __future__ import annotations

import logging
from typing import Dict, List

import torch
import torch.nn as nn

from .paths import CORPUS_FILE

logger = logging.getLogger(__name__)


class CharTokenizer:
    def __init__(self):
        self.char2idx = {'<PAD>': 0, '<UNK>': 1}
        self.idx2char = {0: '<PAD>', 1: '<UNK>'}
        self.num_chars = 2

    def fit(self, texts: List[str]) -> None:
        for text in texts:
            for char in text:
                if char not in self.char2idx:
                    self.char2idx[char] = self.num_chars
                    self.idx2char[self.num_chars] = char
                    self.num_chars += 1

    def encode(self, text: str, max_len: int = 128) -> torch.Tensor:
        indices = [self.char2idx.get(char, 1) for char in text]
        if len(indices) > max_len:
            indices = indices[:max_len]
        else:
            indices += [0] * (max_len - len(indices))
        return torch.tensor(indices)


class VOCTransformer(nn.Module):
    def __init__(self, vocab_size: int, n_classes: int, d_model: int = 256,
                 nhead: int = 8, num_layers: int = 3):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, d_model)
        self.pos_encoder = nn.Embedding(128, d_model)
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=nhead,
            batch_first=True
        )
        self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
        self.fc = nn.Linear(d_model, n_classes)

        # 添加属性占位符
        self.tokenizer = None
        self.label2idx = None
        self.idx2label = None

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        seq_len = x.size(1)
        pos = torch.arange(seq_len, device=x.device).unsqueeze(0).expand(x.size(0), -1)
        x = self.embedding(x)
        x = x + self.pos_encoder(pos)
        x = self.transformer(x)
        x = x.mean(dim=1)
        return self.fc(x)


class CorpusAnalyzer:
    """语料库解析：疑似源名称 → 历史风况记录（含经纬度所在的原文）。"""

    def __init__(self, corpus_file: str | None = None):
        self.corpus_file = str(corpus_file or CORPUS_FILE)
        self.source_weather_info: Dict[str, List[Dict]] = {}
        self._load_corpus()

    def _extract_source_name(self, source: str) -> str:
        """提取疑似源的纯名称（不含坐标）。"""
        if '（' in source:
            return source[:source.find('（')].strip()
        return source.strip()

    def _load_corpus(self) -> None:
        with open(self.corpus_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if '疑似源为' not in line:
                    continue
                parts = line.split('，')
                full_source = line.split('疑似源为')[1]
                source_name = self._extract_source_name(full_source)

                wind_speed = ''
                wind_direction = ''
                for part in parts:
                    if '风速为' in part:
                        wind_speed = part.replace('风速为', '').strip()
                    if '风向为' in part:
                        wind_direction = part.replace('风向为', '').strip()

                self.source_weather_info.setdefault(source_name, []).append({
                    'wind_speed': wind_speed,
                    'wind_direction': wind_direction,
                    'full_text': line,
                })

        logger.info("已加载语料库中 %d 个疑似源的信息", len(self.source_weather_info))

    def get_source_weather_info(self, source: str) -> List[Dict]:
        """按纯名称匹配疑似源的历史风况记录。"""
        return self.source_weather_info.get(self._extract_source_name(source), [])


__all__ = ["CharTokenizer", "VOCTransformer", "CorpusAnalyzer"]
