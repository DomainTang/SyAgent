# -*- coding: utf-8 -*-
import torch
import torch.nn as nn
import torch.nn.functional as F
import logging
import os
import pandas as pd
from datetime import datetime
from typing import Dict, List, Tuple
from torch.utils.data import Dataset, DataLoader
from sklearn.model_selection import train_test_split
import matplotlib.pyplot as plt

from paths import DATA_DIR, MODELS_DIR, OUTPUT_DIR

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
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


class VOCDataset(Dataset):
    def __init__(self, texts: List[str], labels: List[int], tokenizer: CharTokenizer):
        self.texts = texts
        self.labels = labels
        self.tokenizer = tokenizer

    def __len__(self) -> int:
        return len(self.texts)

    def __getitem__(self, idx: int) -> Dict:
        text = self.texts[idx]
        label = self.labels[idx]
        encoded = self.tokenizer.encode(text)
        return {
            'text': encoded,
            'label': torch.tensor(label, dtype=torch.long)
        }


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


class ModelTrainer:
    def __init__(self, model: nn.Module, train_loader: DataLoader,
                 val_loader: DataLoader, device: torch.device):
        self.model = model
        self.train_loader = train_loader
        self.val_loader = val_loader
        self.device = device
        self.optimizer = torch.optim.Adam(model.parameters())
        self.criterion = nn.CrossEntropyLoss()
        self.history = {'train_loss': [], 'val_loss': [], 'val_acc': []}

    def train_epoch(self) -> float:
        self.model.train()
        total_loss = 0
        for batch in self.train_loader:
            texts = batch['text'].to(self.device)
            labels = batch['label'].to(self.device)

            self.optimizer.zero_grad()
            outputs = self.model(texts)
            loss = self.criterion(outputs, labels)
            loss.backward()
            self.optimizer.step()

            total_loss += loss.item()

        return total_loss / len(self.train_loader)

    def validate(self):
        """验证模型"""
        self.model.eval()
        total_loss = 0
        total_samples = 0

        with torch.no_grad():
            for batch in self.val_loader:
                input_ids = batch['input_ids'].to(self.device)
                labels = batch['labels'].to(self.device)

                outputs = self.model(input_ids)
                loss = self.criterion(outputs, labels)

                total_loss += loss.item() * len(input_ids)
                total_samples += len(input_ids)

        return total_loss / total_samples if total_samples > 0 else float('inf')

    def train(self, epochs: int):
        logger.info("开始训练...")
        best_val_acc = 0
        for epoch in range(epochs):
            train_loss = self.train_epoch()
            val_loss, val_acc = self.validate()

            self.history['train_loss'].append(train_loss)
            self.history['val_loss'].append(val_loss)
            self.history['val_acc'].append(val_acc)

            if val_acc > best_val_acc:
                best_val_acc = val_acc

            if (epoch + 1) % 10 == 0:
                logger.info(f'Epoch {epoch + 1}/{epochs}:')
                logger.info(f'训练损失: {train_loss:.4f}')
                logger.info(f'验证损失: {val_loss:.4f}')
                logger.info(f'验证准确率: {val_acc:.4f}')
                # logger.info(f'最佳准确率: {best_val_acc:.4f}\n')

        self._plot_history()

    def _plot_history(self):
        plt.figure(figsize=(12, 4))

        plt.subplot(1, 2, 1)
        plt.plot(self.history['train_loss'], label='训练损失')
        plt.plot(self.history['val_loss'], label='验证损失')
        plt.xlabel('Epoch')
        plt.ylabel('Loss')
        plt.legend()

        plt.subplot(1, 2, 2)
        plt.plot(self.history['val_acc'], label='验证准确率')
        plt.xlabel('Epoch')
        plt.ylabel('Accuracy')
        plt.legend()

        plt.tight_layout()
        plt.savefig('training_history.png')
        plt.close()


class CorpusAnalyzer:
    def __init__(self, corpus_file: str = None):
        if corpus_file is None:
            corpus_file = os.path.join(DATA_DIR, '语料库带经维度2020-2025.txt')
        self.corpus_file = corpus_file
        self.source_weather_info = {}
        self._load_corpus()

    def _extract_source_name(self, source: str) -> str:
        """提取疑似源的纯名称（不含坐标）"""
        if '（' in source:
            return source[:source.find('（')].strip()
        return source.strip()

    def _load_corpus(self) -> None:
        try:
            with open(self.corpus_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if '疑似源为' in line:
                        parts = line.split('，')
                        full_source = line.split('疑似源为')[1]
                        # 提取不含坐标的疑似源名称作为键
                        source_name = self._extract_source_name(full_source)

                        # 提取风速风向信息
                        wind_speed = ''
                        wind_direction = ''
                        for part in parts:
                            if '风速为' in part:
                                wind_speed = part.replace('风速为', '').strip()
                            if '风向为' in part:
                                wind_direction = part.replace('风向为', '').strip()

                        if source_name not in self.source_weather_info:
                            self.source_weather_info[source_name] = []

                        self.source_weather_info[source_name].append({
                            'wind_speed': wind_speed,
                            'wind_direction': wind_direction,
                            'full_text': line
                        })

            logger.info(f"已加载语料库中{len(self.source_weather_info)}个疑似源的信息")

        except Exception as e:
            logger.error(f"加载语料库失败: {str(e)}")
            raise

    def get_source_weather_info(self, source: str) -> List[Dict]:
        """获取疑似源的天气信息，使用纯名称匹配"""
        source_name = self._extract_source_name(source)
        return self.source_weather_info.get(source_name, [])


class PredictionRecorder:
    def __init__(self, corpus_analyzer: CorpusAnalyzer):
        self.corpus_analyzer = corpus_analyzer

    def _extract_coordinates(self, text: str) -> tuple:
        """提取文本中的坐标信息"""
        try:
            if '（' in text and '）' in text:
                coords = text[text.find('（')+1:text.find('）')]
                longitude, latitude = map(str.strip, coords.split(','))
                return longitude, latitude
            return '', ''
        except:
            return '', ''

    def save_to_excel(self, prediction_result: Dict) -> None:
        try:
            current_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            os.makedirs(OUTPUT_DIR, exist_ok=True)
            excel_path = os.path.join(OUTPUT_DIR, f'预测结果_{current_time}.xlsx')

            source = prediction_result['predicted_source']
            weather_records = self.corpus_analyzer.get_source_weather_info(source)

            data = []
            if weather_records:
                for record in weather_records:
                    # 从原始记录中提取坐标
                    longitude, latitude = self._extract_coordinates(record['full_text'])
                    wind_speed = record['wind_speed'].replace('级', '')

                    row = {
                        '预测时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        '传感器信息': prediction_result['input_text'],
                        '预测疑似源': source,
                        '经度': longitude,
                        '纬度': latitude,
                        '置信度': f"{prediction_result['confidence']:.4f}",
                        '风速(级)': wind_speed,
                        '风向': record['wind_direction'],
                        '原始记录': record['full_text']
                    }
                    data.append(row)
            else:
                # 从语料库中查找包含该疑似源的记录以获取坐标
                with open(os.path.join(DATA_DIR, '语料库带经维度2020-2025.txt'), 'r', encoding='utf-8') as f:
                    for line in f:
                        if source in line and '疑似源为' in line:
                            longitude, latitude = self._extract_coordinates(line)
                            data.append({
                                '预测时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                '传感器信息': prediction_result['input_text'],
                                '预测疑似源': source,
                                '经度': longitude,
                                '纬度': latitude,
                                '置信度': f"{prediction_result['confidence']:.4f}",
                                '风速(级)': '',
                                '风向': '',
                                '原始记录': '未找到相关记录'
                            })
                            break
                    else:
                        # 如果在语料库中也找不到，添加空坐标记录
                        data.append({
                            '预测时间': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            '传感器信息': prediction_result['input_text'],
                            '预测疑似源': source,
                            '经度': '',
                            '纬度': '',
                            '置信度': f"{prediction_result['confidence']:.4f}",
                            '风速(级)': '',
                            '风向': '',
                            '原始记录': '未找到相关记录'
                        })

            df = pd.DataFrame(data)
            with pd.ExcelWriter(excel_path, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='预测结果')

            logger.info(f"预测结果已保存到: {excel_path}")

        except Exception as e:
            logger.error(f"保存Excel文件失败: {str(e)}")


class SinglePredictor:
    def __init__(self, model_path: str = None):
        if model_path is None:
            model_path = os.path.join(MODELS_DIR, 'voc_model.pth')
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        try:
            self._load_model(model_path)
            logger.info(f'成功加载模型，使用设备: {self.device}')
        except Exception as e:
            logger.error(f'模型加载失败: {str(e)}')
            raise

    def _load_model(self, model_path: str) -> None:
        checkpoint = torch.load(model_path, map_location=self.device, weights_only=False)  # 模型为本地可信文件，兼容 PyTorch 2.6+
        self.tokenizer = checkpoint['tokenizer']
        self.source2idx = checkpoint['source2idx']
        self.idx2source = {v: k for k, v in self.source2idx.items()}

        self.model = VOCTransformer(
            vocab_size=self.tokenizer.num_chars,
            n_classes=len(self.source2idx)
        ).to(self.device)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.model.eval()

    def predict(self, text: str) -> Dict:
        try:
            encoded = self.tokenizer.encode(text).unsqueeze(0).to(self.device)

            with torch.no_grad():
                outputs = self.model(encoded)
                probs = F.softmax(outputs, dim=1)
                pred_idx = outputs.argmax(dim=1).item()
                confidence = probs[0][pred_idx].item()
                predicted_source = self.idx2source[pred_idx]

                top3 = torch.topk(probs, min(3, len(self.source2idx)))
                top3_results = [
                    (self.idx2source[idx.item()], prob.item())
                    for prob, idx in zip(top3.values[0], top3.indices[0])
                ]

                return {
                    'status': 'success',
                    'input_text': text,
                    'predicted_source': predicted_source,
                    'confidence': confidence,
                    'top3_results': top3_results
                }

        except Exception as e:
            logger.error(f'预测过程发生错误: {str(e)}')
            return {
                'status': 'error',
                'input_text': text,
                'error_message': str(e)
            }


def print_prediction(result: Dict) -> None:
    print("\n=== 预测结果 ===")
    print(f"输入文本: {result['input_text']}")

    if result['status'] == 'error':
        print(f"错误: {result['error_message']}")
        return

    print(f"预测的疑似源: {result['predicted_source']}")
    print(f"置信度: {result['confidence']:.4f}")

    print("\nTop-3预测结果:")
    for i, (source, prob) in enumerate(result['top3_results'], 1):
        print(f"{i}. {source}: {prob:.4f}")


def main():
    model_path = os.path.join(MODELS_DIR, 'voc_model.pth')

    # 初始化语料分析器和预测记录器
    corpus_analyzer = CorpusAnalyzer()
    prediction_recorder = PredictionRecorder(corpus_analyzer)

    # 检查模型是否存在
    if os.path.exists(model_path):
        logger.info(f"找到已有模型: {model_path}")
        predictor = SinglePredictor(model_path)
    else:
        logger.info("未找到模型，开始训练新模型...")
        try:
            # 1. 准备数据
            texts = []
            sources = []

            with open(os.path.join(DATA_DIR, '语料库带经维度2020-2025.txt'), 'r', encoding='utf-8') as f:
                for line in f:
                    text = line.strip()
                    parts = text.split('，')
                    if '疑似源为' in text:
                        sensor_part = next((p for p in parts if '传感器' in p), '')
                        location_part = next((p for p in parts if '位置为' in p), '')
                        source = text.split('疑似源为')[1]

                        if sensor_part and location_part:
                            train_text = f"{sensor_part}，{location_part}"
                            texts.append(train_text)
                            sources.append(source)

            logger.info(f"已加载{len(texts)}条训练数据")

            # 2. 数据预处理
            unique_sources = list(set(sources))
            source2idx = {source: idx for idx, source in enumerate(unique_sources)}
            labels = [source2idx[source] for source in sources]


            # 3. 划分数据集
            train_texts, temp_texts, train_labels, temp_labels = train_test_split(
                texts, labels, test_size=0.3, random_state=42
            )
            val_texts, test_texts, val_labels, test_labels = train_test_split(
                temp_texts, temp_labels, test_size=0.5, random_state=42
            )

            # 4. 准备训练
            tokenizer = CharTokenizer()
            tokenizer.fit(train_texts)

            train_dataset = VOCDataset(train_texts, train_labels, tokenizer)
            val_dataset = VOCDataset(val_texts, val_labels, tokenizer)

            train_loader = DataLoader(train_dataset, batch_size=32, shuffle=True)
            val_loader = DataLoader(val_dataset, batch_size=32)

            # 5. 训练模型
            device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
            model = VOCTransformer(
                vocab_size=tokenizer.num_chars,
                n_classes=len(unique_sources)
            ).to(device)

            trainer = ModelTrainer(model, train_loader, val_loader, device)
            trainer.train(epochs=200)

            # 6. 保存模型
            torch.save({
                'model_state_dict': model.state_dict(),
                'tokenizer': tokenizer,
                'source2idx': source2idx,
            }, model_path)
            logger.info("模型已保存")

            predictor = SinglePredictor(model_path)

        except Exception as e:
            logger.error(f"模型训练失败: {str(e)}")
            return

    # 测试预测
    test_texts = [
        "传感器2，位置为2#~7#罐区-401罐北侧灯杆，风速为3级，风向为东北风"
    ]

    print("\n开始测试预测...")
    for text in test_texts:
        result = predictor.predict(text)
        print_prediction(result)
        prediction_recorder.save_to_excel(result)
        print("\n" + "=" * 50)


if __name__ == "__main__":
    main()
