"""统一日志与可观测性配置。

服务化部署前的扩展点：
    - 当前：统一格式、级别、按模块获取 logger（替代散落各处的 print）。
    - 后续：可在此把 handler 换成 JSON 结构化输出（便于 ELK/Loki 采集），
      或在 handler 上挂 OpenTelemetry 的日志/指标桥接，实现全链路可观测。

用法：
    from logging_config import get_logger
    logger = get_logger(__name__)
"""
import logging
import os
import sys
from typing import Optional

_configured = False


def setup_logging(level: Optional[str] = None) -> None:
    """幂等地初始化根日志配置（首次调用生效，可被 LOG_LEVEL/LOG_FORMAT 覆盖）。"""
    global _configured
    if _configured:
        return

    lvl = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    fmt = os.getenv(
        "LOG_FORMAT",
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.basicConfig(
        level=getattr(logging, lvl, logging.INFO),
        format=fmt,
        stream=sys.stdout,
        force=True,
    )
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """获取模块级 logger（自动完成初始化）。"""
    setup_logging()
    return logging.getLogger(name)
