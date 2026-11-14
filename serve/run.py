"""统一启动入口：启动 FastAPI HTTP 服务。

用法：
    python serve/run.py                        # 默认 0.0.0.0:8000
    python serve/run.py --port 8080            # 指定端口
    python serve/run.py --host 127.0.0.1       # 仅本机访问
"""
from __future__ import annotations

import argparse
import os
import sys

# 保证从任意目录运行都能导入项目模块（config/agents/utils/workflow 走顶层平铺导入）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    p = argparse.ArgumentParser(description="启动专利写作 Agent HTTP 服务")
    p.add_argument("--host", default=os.getenv("SERVE_HOST", "0.0.0.0"))
    p.add_argument("--port", type=int, default=int(os.getenv("SERVE_PORT", "8000")))
    p.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = p.parse_args()

    import uvicorn

    uvicorn.run("serve.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
