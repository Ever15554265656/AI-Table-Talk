"""兼容入口：建表 + 写入样例数据（真正的实现已迁到 ``backend/seeds.py``）。

两种用法等价：

    python -m backend.seeds            # 推荐：无需先改 sys.path
    python scripts/init_db.py          # 兼容旧脚本 / 旧文档

参数（``--reset``）与输出完全一致，见 :mod:`backend.seeds`。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许 `python scripts/init_db.py` 直接运行（scripts/ 不在包内）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.seeds import SAMPLE_SESSIONS, main, seed  # noqa: E402,F401

if __name__ == "__main__":
    raise SystemExit(main())
