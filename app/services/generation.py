"""基于检索上下文的 Prompt 构建和 GPT 回答生成服务。"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter, sleep
from typing import Any

from loguru import logger
from openai import OpenAI

from app.core.config import settings
from app.services.retrieval import RetrievalItem

NO_EVIDENCE_ANSWER = "文档中没有足够依据"
GENERATION_FAILURE_ANSWER = "回答生成失败，请稍后重试"
SYSTEM_PROMPT = """你是 Prag 知识库问答助手。
你只能依据用户提供的知识库上下文回答问题，不得使用上下文之外的知识进行补充或猜测。
如果上下文不足以确认答案，只输出：文档中没有足够依据
回答中需要引用依据时，使用上下文中的 [1]、[2] 等引用编号，不得伪造引用编号。
请直接回答问题，不要描述你的推理过程。"""


class GenerationError(RuntimeError):
    """表示 GPT 回答生成失败。"""


@dataclass(frozen=True)
class GenerationResult:
    """一次回答生成的结果和调用统计。"""

    answer: str
    context: str
    context_token_count: int
    duration_ms: int
    retried: bool
    used_context: bool


class GPTClient:
    """通过 OpenAI 兼容接口调用 GPT，并按配置重试一次。"""

    def __init__(self, client: Any | None = None) -> None:
        """初始化 GPT 客户端，可注入 fake client 进行测试。"""
        self.client = client or OpenAI(
            api_key=settings.gpt_api_key,
            base_url=settings.gpt_base_url,
            timeout=settings.gpt_timeout_seconds,
            max_retries=0,
        )
        self.retry_count = 0

    def complete(self, question: str, context: str) -> tuple[str, bool]:
        """依据系统 Prompt、上下文和问题生成回答，并返回是否重试。"""
        last_error: Exception | None = None
        for attempt in range(settings.gpt_max_retries + 1):
            try:
                response = self.client.chat.completions.create(
                    model=settings.gpt_chat_model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"知识库上下文：\n{context}\n\n用户问题：\n{question}"},
                    ],
                    temperature=settings.gpt_temperature,
                    max_tokens=settings.gpt_max_tokens,
                    timeout=settings.gpt_timeout_seconds,
                )
                answer = response.choices[0].message.content
                if not answer or not answer.strip():
                    raise GenerationError("GPT 返回空回答")
                return answer.strip(), attempt > 0
            except GenerationError:
                raise
            except Exception as error:
                last_error = error
                if attempt >= settings.gpt_max_retries:
                    break
                self.retry_count += 1
                sleep(settings.gpt_retry_delay_seconds)
        raise GenerationError("回答生成失败") from last_error


class AnswerGenerator:
    """将检索结果格式化为上下文并生成受依据约束的回答。"""

    def __init__(self, gpt_client: GPTClient | Any | None = None) -> None:
        """初始化回答生成器，可注入 GPT 客户端。"""
        self.gpt_client = gpt_client or GPTClient()

    def generate(self, question: str, retrieval_items: list[RetrievalItem]) -> GenerationResult:
        """生成回答；没有检索依据时不调用 GPT 并返回固定提示。"""
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("问题不能为空")
        started_at = perf_counter()
        context = build_context(retrieval_items)
        context_token_count = estimate_token_count(context)
        if not retrieval_items:
            result = GenerationResult(
                answer=NO_EVIDENCE_ANSWER,
                context=context,
                context_token_count=0,
                duration_ms=int((perf_counter() - started_at) * 1000),
                retried=False,
                used_context=False,
            )
            self._log_result(result, status="no_evidence")
            return result
        try:
            answer, retried = self.gpt_client.complete(normalized_question, context)
        except GenerationError:
            result = GenerationResult(
                answer=GENERATION_FAILURE_ANSWER,
                context=context,
                context_token_count=context_token_count,
                duration_ms=int((perf_counter() - started_at) * 1000),
                retried=getattr(self.gpt_client, "retry_count", 0) > 0,
                used_context=True,
            )
            self._log_result(result, status="failed")
            return result
        result = GenerationResult(
            answer=answer,
            context=context,
            context_token_count=context_token_count,
            duration_ms=int((perf_counter() - started_at) * 1000),
            retried=retried,
            used_context=True,
        )
        self._log_result(result, status="success")
        return result

    def _log_result(self, result: GenerationResult, status: str) -> None:
        """记录上下文规模、生成耗时和重试状态，不记录完整问题或上下文。"""
        logger.bind(
            event="llm_generation",
            context_token_count=result.context_token_count,
            duration_ms=result.duration_ms,
            retried=result.retried,
            status=status,
        ).info("llm generation completed")


def build_context(items: list[RetrievalItem]) -> str:
    """将检索结果按稳定编号拼接为 Prompt 上下文。"""
    sections: list[str] = []
    for index, item in enumerate(items, start=1):
        location = [f"文档：{item.document_name}", f"chunk：{item.chunk_index}"]
        if item.page_number is not None:
            location.append(f"页码：{item.page_number}")
        if item.section_title:
            location.append(f"章节：{item.section_title}")
        if item.line_number is not None:
            location.append(f"行号：{item.line_number}")
        parent = f"\n父级上下文：{item.parent_content}" if item.parent_content else ""
        sections.append(f"[{index}] {' | '.join(location)}\n内容：{item.content}{parent}")
    return "\n\n".join(sections)


def estimate_token_count(context: str) -> int:
    """按配置的字符/token 比例估算上下文 token 数。"""
    if not context:
        return 0
    return max(1, (len(context) + settings.gpt_token_char_ratio - 1) // settings.gpt_token_char_ratio)
