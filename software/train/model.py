"""KWS-TinyCNN-8-Flat 模型定义（FP32 训练版，含 BatchNorm，导出时折叠）。

默认网络与 docs/ARCHITECTURE.md 第 2 节一致：
  Input (1,20,16) -> Conv1(8,3x3,SAME) + BN + ReLU -> MaxPool(2x2)
                   -> Conv2(8,3x3,SAME) + BN + ReLU -> MaxPool(2x2)
                   -> NHWC Flatten(160) -> FC(160 -> num_classes)

`head='gap'` 仅保留用于复现旧实验，部署导出器只接受默认 Flatten 头。
"""

import torch
import torch.nn as nn


class TinyCNN8(nn.Module):
    def __init__(self, num_classes=4, in_channels=1, head='flatten'):
        super().__init__()
        if head not in ('gap', 'flatten'):
            raise ValueError(f'unsupported classifier head: {head}')
        self.head = head
        # 卷积不带 bias：bias 由 BN 折叠产生（见导出器）
        self.conv1 = nn.Conv2d(in_channels, 8, kernel_size=3, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(8)
        self.conv2 = nn.Conv2d(8, 8, kernel_size=3, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(8)
        self.pool = nn.MaxPool2d(kernel_size=2, stride=2)
        self.relu = nn.ReLU(inplace=False)
        # FC 直接训练 bias，导出时量化为 INT32（FC 不 requant）
        self.fc = nn.Linear(8 if head == 'gap' else 5 * 4 * 8,
                            num_classes, bias=True)
        self.reset_parameters()

    def reset_parameters(self):
        """显式固定初始化策略，避免依赖 PyTorch 各版本的默认值。"""
        nn.init.kaiming_uniform_(self.conv1.weight, nonlinearity='relu')
        nn.init.kaiming_uniform_(self.conv2.weight, nonlinearity='relu')
        nn.init.ones_(self.bn1.weight)
        nn.init.zeros_(self.bn1.bias)
        nn.init.ones_(self.bn2.weight)
        nn.init.zeros_(self.bn2.bias)
        nn.init.xavier_uniform_(self.fc.weight)
        nn.init.zeros_(self.fc.bias)

    def forward(self, x):
        # x: (B, in_channels, 20, 16)
        x = self.relu(self.bn1(self.conv1(x)))   # (B,8,20,16)
        x = self.pool(x)                          # (B,8,10,8)
        x = self.relu(self.bn2(self.conv2(x)))   # (B,8,10,8)
        x = self.pool(x)                          # (B,8,5,4)
        if self.head == 'gap':
            x = x.mean(dim=(2, 3))                # global avg pool -> (B,8)
        else:
            # 与 NPU 激活 SRAM 的 NHWC 线性顺序一致，便于后续直接部署。
            x = x.permute(0, 2, 3, 1).contiguous().flatten(1)  # (B,160)
        x = self.fc(x)                            # (B, num_classes)
        return x


def count_params(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


if __name__ == '__main__':
    m = TinyCNN8()
    x = torch.randn(2, 1, 20, 16)
    y = m(x)
    print('output shape:', tuple(y.shape))
    print('trainable params:', count_params(m))
