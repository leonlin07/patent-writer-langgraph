"""
专利写作智能体配置文件
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 项目根目录（本文件所在目录），供各模块定位 data/output/.env 等资源
BASE_DIR = Path(__file__).resolve().parent

# 显式加载项目根 .env：不依赖进程 CWD，保证从任意目录、任意服务入口运行时配置一致
load_dotenv(BASE_DIR / ".env")

# LLM配置
LLM_CONFIG = {
    "openai": {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "default_model": os.getenv("OPENAI_MODEL", "gpt-4"),
        "enable_thinking": os.getenv("OPENAI_ENABLE_THINKING", "false").lower() == "true",
    },
    "anthropic": {
        "api_key": os.getenv("ANTHROPIC_API_KEY"),
        "default_model": os.getenv("ANTHROPIC_MODEL", "claude-3-opus-20240229"),
        "enable_thinking": os.getenv("ANTHROPIC_ENABLE_THINKING", "false").lower() == "true",
    },
    "siliconflow": {
        "api_key": os.getenv("SILICONFLOW_API_KEY"),
        "base_url": "https://api.siliconflow.cn/v1",
        "default_model": os.getenv("SILICONFLOW_MODEL", "Qwen/Qwen3.5-122B-A10B"),
        # 【关键】硅基流动上的 Qwen3/Qwen3.5 系列默认为思考模型。
        # 开启时 reasoning token 会挤占 max_tokens 预算，导致 finish_reason=length
        # 且 content 为空字符串（不报错），是历史产物中"权利要求/摘要全空"的根因。
        # 专利撰写属于格式化生成任务，无需深度推理，默认关闭。
        "enable_thinking": os.getenv("SILICONFLOW_ENABLE_THINKING", "false").lower() == "true",
    },
}

# 默认使用的LLM提供商
DEFAULT_LLM_PROVIDER = os.getenv("DEFAULT_LLM_PROVIDER", "siliconflow")

# 空响应/失败重试配置
LLM_RETRY_CONFIG = {
    "max_retries": int(os.getenv("LLM_MAX_RETRIES", "3")),   # 最多尝试次数
    "retry_backoff": float(os.getenv("LLM_RETRY_BACKOFF", "2.0")),  # 退避倍数
    "base_wait": float(os.getenv("LLM_BASE_WAIT", "1.5")),   # 首次等待秒数
    # 重试时的兜底策略：逐步放宽 thinking 开关与 max_tokens
    "fallback_disable_thinking": True,
    "fallback_token_scale": 3.0,   # 重试时 max_tokens 放大倍数
}

# 搜索API配置
SEARCH_CONFIG = {
    "serpapi": {
        "api_key": os.getenv("SERPAPI_API_KEY"),
    },
    "exa": {
        "api_key": os.getenv("EXA_API_KEY"),
    },
}

# 专利撰写配置
PATENT_CONFIG = {
    # 权利要求书配置
    "claims": {
        "max_independent_claims": 3,  # 最大独立权利要求数量
        "max_dependent_claims": 10,   # 最大从属权利要求数量
        "min_claims": 5,              # 最少权利要求数量
    },
    # 具体实施方式配置
    "description": {
        "min_sections": 3,            # 最少章节数
        "include_examples": True,     # 是否包含实施例
        "min_examples": 1,            # 最少实施例数
    },
    # 摘要配置
    "abstract": {
        "max_length": 300,            # 最大字数（中文）
    },
}

# 输入解析的字符上限（防止长交底书被静默截断）
# 经验值：一份完整交底书约 2000-6000 字；12000 可覆盖绝大多数场景，
# 且仍在主流模型的上下文安全区内（约 8k-12k token）。
PARSE_MAX_CHARS = int(os.getenv("PARSE_MAX_CHARS", "12000"))

# 输出配置
OUTPUT_CONFIG = {
    "default_output_dir": "output",
    "supported_formats": ["markdown", "docx", "txt"],
    "default_format": "markdown",
}

# token 成本计量单价（单位：元 / 百万 tokens）
# 已按硅基流动 Qwen3.5-122B-A10B 价目表核实（2026-11-10）：
#   输入 ¥0.80 / 输出 ¥6.40（<128k 上下文档位）
# 可被环境变量覆盖；切换模型/档位时须重新核实并更新。
TOKEN_PRICE = {
    "input_per_1m": float(os.getenv("LLM_INPUT_PRICE_PER_1M", "0.8")),
    "output_per_1m": float(os.getenv("LLM_OUTPUT_PRICE_PER_1M", "6.4")),
}

# 提示词模板路径
PROMPT_TEMPLATES = {
    "input_parser": "prompts/input_parser.txt",
    "patent_search": "prompts/patent_search.txt",
    "outline_generator": "prompts/outline_generator.txt",
    "claims_writer": "prompts/claims_writer.txt",
    "description_writer": "prompts/description_writer.txt",
    "abstract_writer": "prompts/abstract_writer.txt",
    "diagram_generator": "prompts/diagram_generator.txt",
}
