"""
LLM客户端模块
支持OpenAI、Anthropic和硅基流动 API
"""
import os
import time
from typing import Optional, Dict, Any, List
import openai
from config import LLM_CONFIG, DEFAULT_LLM_PROVIDER, LLM_RETRY_CONFIG
from logging_config import get_logger
logger = get_logger(__name__)


class LLMClient:
    """LLM客户端类"""

    def __init__(self, provider: Optional[str] = None):
        """
        初始化LLM客户端

        Args:
            provider: LLM提供商，可选 'openai'、'anthropic' 或 'siliconflow'
        """
        self.provider = provider or DEFAULT_LLM_PROVIDER
        self.config = LLM_CONFIG.get(self.provider, {})
        self.enable_thinking = self.config.get("enable_thinking", False)
        self.retry = LLM_RETRY_CONFIG
        # token 用量累计（供成本计量）。注意：全局单例，多任务并发时会互相累加，
        # 精确计量请在「串行评测」场景下先 reset_usage() 再读取 get_usage()。
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        
        if self.provider == "openai":
            self.client = openai.OpenAI(
                api_key=self.config.get("api_key"),
                base_url=self.config.get("base_url")
            )
            self.default_model = self.config.get("default_model", "gpt-4")
        elif self.provider == "anthropic":
            # 惰性导入：项目默认走硅基流动/OpenAI，不强制安装 anthropic
            anthropic = self._import_anthropic()
            self.client = anthropic.Anthropic(
                api_key=self.config.get("api_key")
            )
            self.default_model = self.config.get("default_model", "claude-3-opus-20240229")
        elif self.provider == "siliconflow":
            # 硅基流动使用OpenAI兼容接口
            self.client = openai.OpenAI(
                api_key=self.config.get("api_key"),
                base_url=self.config.get("base_url", "https://api.siliconflow.cn/v1")
            )
            self.default_model = self.config.get("default_model", "Qwen/Qwen3.5-122B-A10B")
        else:
            raise ValueError(f"不支持的LLM提供商: {self.provider}")
    
    @staticmethod
    def _import_anthropic():
        """
        惰性导入 anthropic SDK

        anthropic 为可选依赖（默认走硅基流动/OpenAI），未安装时给出明确提示。
        """
        try:
            import anthropic as _anthropic
        except ImportError:
            raise ImportError("使用 anthropic 提供商需先安装: pip install anthropic")
        return _anthropic
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """
        发送聊天请求（带空响应检测与自动重试）

        修复说明：
            思考型模型（如 Qwen3/Qwen3.5）在开启 thinking 时，推理 token 会挤占
            max_tokens 预算，导致 finish_reason='length' 且 content 为空字符串。
            这种情况**不抛异常**，历史上会被下游静默接受，产出空的权利要求/摘要。
            本方法对空响应视为失败并自动重试（先关思考、再放大 token 预算），
            最终仍失败则抛异常，交由各 Agent 的 except 分支走兜底模板。

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大token数
            **kwargs: 其他参数（支持 enable_thinking 覆盖全局配置）

        Returns:
            生成的文本（保证非空）

        Raises:
            Exception: 重试耗尽仍无有效内容时抛出
        """
        model = model or self.default_model
        enable_thinking = kwargs.pop("enable_thinking", self.enable_thinking)

        max_retries = max(1, int(self.retry.get("max_retries", 3)))
        base_wait = float(self.retry.get("base_wait", 1.5))
        backoff = float(self.retry.get("retry_backoff", 2.0))
        token_scale = float(self.retry.get("fallback_token_scale", 3.0))

        last_reason = "未知原因"

        for attempt in range(max_retries):
            # 重试策略：首次按全局配置；后续逐步关闭思考并放大 token 预算
            use_thinking = enable_thinking
            use_max_tokens = max_tokens
            if attempt > 0:
                if self.retry.get("fallback_disable_thinking", True):
                    use_thinking = False
                if use_max_tokens:
                    use_max_tokens = int(use_max_tokens * (token_scale ** (attempt - 1)))

            try:
                content = self._dispatch(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=use_max_tokens,
                    enable_thinking=use_thinking,
                    **kwargs
                )

                if content and content.strip():
                    return content.strip()

                # 空响应 —— 视为失败
                last_reason = (
                    f"第{attempt + 1}次尝试返回空内容"
                    f"(thinking={use_thinking}, max_tokens={use_max_tokens})"
                )
                logger.warning(f"[WARN] LLM返回空内容，{last_reason}，准备重试...")

            except Exception as e:
                last_reason = f"第{attempt + 1}次尝试异常: {str(e)}"
                logger.warning(f"[WARN] LLM调用失败: {last_reason}")

            if attempt < max_retries - 1:
                time.sleep(base_wait * (backoff ** attempt))

        raise Exception(f"LLM请求失败（已重试{max_retries}次），最后原因: {last_reason}")

    def _dispatch(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: float,
        max_tokens: Optional[int],
        enable_thinking: bool,
        **kwargs
    ) -> str:
        """
        按 provider 分发实际请求

        Returns:
            模型返回的文本内容（可能为空字符串，由调用方校验）
        """
        # 思考开关通过 OpenAI 兼容协议的 extra_body 传递（Anthropic 原生不支持该字段）
        extra_body = {"enable_thinking": enable_thinking}

        if self.provider in ("openai", "siliconflow"):
            response = self.client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                extra_body=extra_body,
                **kwargs
            )
            self._record_usage(getattr(response, "usage", None))
            return response.choices[0].message.content or ""

        elif self.provider == "anthropic":
            # 转换消息格式：Anthropic 要求 system 单独传参
            system_message = ""
            user_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_message = msg["content"]
                else:
                    user_messages.append(msg)

            response = self.client.messages.create(
                model=model,
                messages=user_messages,
                system=system_message if system_message else None,
                temperature=temperature,
                max_tokens=max_tokens or 4096,
                **kwargs
            )
            self._record_usage(getattr(response, "usage", None))
            return response.content[0].text or ""

        raise ValueError(f"不支持的LLM提供商: {self.provider}")

    def _record_usage(self, usage) -> None:
        """把一次调用的 usage 累加到实例计数器（兼容 openai 与 anthropic 字段名）。"""
        if usage is None:
            return
        prompt = getattr(usage, "prompt_tokens", None)
        if prompt is None:
            prompt = getattr(usage, "input_tokens", 0)
        completion = getattr(usage, "completion_tokens", None)
        if completion is None:
            completion = getattr(usage, "output_tokens", 0)
        self.usage["prompt_tokens"] += int(prompt or 0)
        self.usage["completion_tokens"] += int(completion or 0)
        self.usage["calls"] += 1

    def get_usage(self) -> dict:
        """返回累计 token 用量（prompt/completion/calls）。"""
        return dict(self.usage)

    def reset_usage(self) -> None:
        """清零计数器（串行评测每个 case 前调用，得到单次成本）。"""
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}

    def estimate_cost(self) -> float:
        """按 config.TOKEN_PRICE 估算累计调用的成本（单位：元）。"""
        from config import TOKEN_PRICE
        return (
            self.usage["prompt_tokens"] / 1e6 * TOKEN_PRICE["input_per_1m"]
            + self.usage["completion_tokens"] / 1e6 * TOKEN_PRICE["output_per_1m"]
        )

    def complete(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        完成文本生成
        
        Args:
            prompt: 提示词
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大token数
            system_prompt: 系统提示词
            **kwargs: 其他参数
            
        Returns:
            生成的文本
        """
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        return self.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs
        )


# 全局LLM客户端实例
_llm_client: Optional[LLMClient] = None


def get_llm_client(provider: Optional[str] = None) -> LLMClient:
    """获取全局LLM客户端实例"""
    global _llm_client
    if _llm_client is None or provider:
        _llm_client = LLMClient(provider)
    return _llm_client
