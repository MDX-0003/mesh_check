import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 仓库根进 sys.path：`meshq` 是顶层包，测试与脚本一律绝对导入（from meshq.core.common import …）
sys.path.insert(0, str(ROOT))
