"""synthetic：只在显式收集公开实验测试时开放本 fixture 的固定源码目录。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "source"))
