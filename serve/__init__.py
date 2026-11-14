"""服务化部署包：FastAPI 服务层 + 异步任务管理 + Gradio 前端。

对外入口：
    python serve/run.py            # 启动 FastAPI HTTP 服务
    python serve/ui.py             # 启动 Gradio 交互界面
    uvicorn serve.app:app          # 标准 uvicorn 启动方式
"""
