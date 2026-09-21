# -*- coding: utf-8 -*-
"""统一路径配置：所有数据/模型/输出均按此定位，便于目录重组与跨机器运行。"""
import os

# 本文件位于 app/ 下，工程根目录为其上一级
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(APP_DIR)

DATA_DIR = os.environ.get('SHIHUA_DATA_DIR') or os.path.join(PROJECT_ROOT, 'data')
MODELS_DIR = os.environ.get('SHIHUA_MODELS_DIR') or os.path.join(PROJECT_ROOT, 'models')
OUTPUT_DIR = os.environ.get('SHIHUA_OUTPUT_DIR') or os.path.join(PROJECT_ROOT, 'output')

# 主要运行文件
CORPUS_FILE = os.path.join(DATA_DIR, '语料库带经维度2020-2025.txt')
KML_FILE = os.path.join(DATA_DIR, '安庆监测点位及分区 202403.kml')
MODEL_FILE = os.path.join(MODELS_DIR, 'voc_model.pth')
MODEL_RETRAINED_FILE = os.path.join(MODELS_DIR, 'voc_model_retrained.pth')
PLUME_HTML = os.path.join(OUTPUT_DIR, '烟羽扩散可视化.html')
PREDICT_XLSX = os.path.join(OUTPUT_DIR, '预测结果.xlsx')


def ensure_dirs():
    """确保数据/模型/输出目录存在（数据目录通常由仓库自带或手动放入）。"""
    for d in (DATA_DIR, MODELS_DIR, OUTPUT_DIR):
        os.makedirs(d, exist_ok=True)
