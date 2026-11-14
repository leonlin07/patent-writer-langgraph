"""请求/响应 Pydantic 模型（服务层数据契约）。

与 workflow/state.py 的 PatentState 解耦：HTTP 层只暴露对业务方友好的字段，
内部再映射到 run_pipeline 的参数，避免把 LangGraph 内部状态泄漏给调用方。
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    """POST /generate 请求体。"""

    input_path: Optional[str] = Field(default=None, description="技术交底书路径（阶段B入口）")
    project_dir: Optional[str] = Field(default=None, description="项目目录（阶段A入口）")
    hint: str = Field(default="", description="项目方向提示（阶段A强烈建议填写）")
    output_format: str = Field(default="markdown", description="输出格式：markdown/text/docx")
    do_search: bool = Field(default=False, description="是否执行专利检索")
    save_individual: bool = Field(default=True, description="是否分别保存各部分文档")
    max_files: int = Field(default=40, ge=1, le=200, description="阶段A最多读取文件数")
    disclosure_path: str = Field(default="", description="交底书输出路径（阶段A）")


class GenerateResponse(BaseModel):
    """POST /generate 响应体：立即返回 job_id，生成在后台异步执行。"""

    job_id: str
    status: str = "pending"
    status_url: str = ""
    result_url: str = ""


class StatusResponse(BaseModel):
    """GET /status/{job_id} 响应体。"""

    job_id: str
    status: str  # pending / running / succeeded / failed
    completed_nodes: List[str] = Field(default_factory=list)
    progress: float = 0.0  # 0.0 ~ 1.0（按已完成节点数估算）
    created_at: Optional[float] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None


class ResultResponse(BaseModel):
    """GET /result/{job_id} 响应体。"""

    job_id: str
    status: str
    files: Dict[str, str] = Field(default_factory=dict, description="产物文件路径映射")
    errors: List[str] = Field(default_factory=list, description="非致命错误列表")
    success: bool = False
    error: Optional[str] = None


class HealthResponse(BaseModel):
    """GET /health 响应体。"""

    status: str
    service: str
    version: str
