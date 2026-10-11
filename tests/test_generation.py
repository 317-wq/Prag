"""Prompt 构建和 GPT 回答生成服务测试。"""

import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault("QWEN_API_KEY", "test-qwen-key")
os.environ.setdefault("GPT_API_KEY", "test-gpt-key")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")

from app.config import settings
from app.services.generation import (
    GENERATION_FAILURE_ANSWER,
    NO_EVIDENCE_ANSWER,
    AnswerGenerator,
    GPTClient,
    build_context,
    estimate_token_count,
)
from app.services.retrieval import RetrievalItem


class FakeChatCompletions:
    """模拟 OpenAI Chat Completions 接口。"""

    def __init__(self, answer: str = "依据 [1]，答案是测试结果。", failures: int = 0) -> None:
        """保存回答和需要模拟的失败次数。"""
        self.answer = answer
        self.failures = failures
        self.calls: list[dict] = []

    def create(self, **kwargs):
        """记录调用参数并返回模拟响应。"""
        self.calls.append(kwargs)
        if self.failures:
            self.failures -= 1
            raise RuntimeError("temporary failure")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.answer))]
        )


class FakeChatClient:
    """模拟 OpenAI 客户端的 chat 资源。"""

    def __init__(self, completions: FakeChatCompletions) -> None:
        """保存 chat completions 资源。"""
        self.chat = SimpleNamespace(completions=completions)


def create_item() -> RetrievalItem:
    """创建带引用元数据的检索结果。"""
    from uuid import uuid4

    return RetrievalItem(
        chunk_id=uuid4(),
        document_id=uuid4(),
        content="测试内容",
        parent_content="标题：测试",
        similarity=0.9,
        document_name="知识库.md",
        chunk_index=2,
        page_number=3,
        section_title="测试章节",
        line_number=12,
    )


def test_build_context_has_stable_reference_and_metadata():
    """上下文按编号拼接内容和引用元数据。"""
    context = build_context([create_item()])

    assert context.startswith("[1] 文档：知识库.md | chunk：2 | 页码：3 | 章节：测试章节 | 行号：12")
    assert "内容：测试内容" in context
    assert "父级上下文：标题：测试" in context


def test_answer_generator_returns_no_evidence_without_calling_gpt():
    """无检索结果时返回固定提示且不调用 GPT。"""
    completions = FakeChatCompletions()
    result = AnswerGenerator(GPTClient(FakeChatClient(completions))).generate("问题", [])

    assert result.answer == NO_EVIDENCE_ANSWER
    assert result.used_context is False
    assert result.context_token_count == 0
    assert completions.calls == []


def test_answer_generator_uses_context_and_records_generation_metrics():
    """有依据时调用 GPT，传入系统 Prompt 和带编号上下文。"""
    completions = FakeChatCompletions()
    result = AnswerGenerator(GPTClient(FakeChatClient(completions))).generate("用户问题", [create_item()])

    request = completions.calls[0]
    assert result.answer == "依据 [1]，答案是测试结果。"
    assert result.used_context is True
    assert result.context_token_count == estimate_token_count(result.context)
    assert request["model"] == settings.gpt_chat_model
    assert request["messages"][0]["role"] == "system"
    assert "只能依据用户提供的知识库上下文" in request["messages"][0]["content"]
    assert "[1]" in request["messages"][1]["content"]
    assert "用户问题" in request["messages"][1]["content"]


def test_gpt_client_retries_once_and_reports_retry():
    """GPT 临时失败时按配置重试，并报告已重试。"""
    original_delay = settings.gpt_retry_delay_seconds
    settings.gpt_retry_delay_seconds = 0
    try:
        completions = FakeChatCompletions(failures=1)
        client = GPTClient(FakeChatClient(completions))
        answer, retried = client.complete("问题", "[1] 内容")
    finally:
        settings.gpt_retry_delay_seconds = original_delay

    assert answer == "依据 [1]，答案是测试结果。"
    assert retried is True
    assert client.retry_count == 1
    assert len(completions.calls) == 2


def test_answer_generator_returns_unified_failure_answer():
    """GPT 重试耗尽时返回统一失败提示，不泄露底层异常。"""
    original_retries = settings.gpt_max_retries
    original_delay = settings.gpt_retry_delay_seconds
    settings.gpt_max_retries = 1
    settings.gpt_retry_delay_seconds = 0
    try:
        completions = FakeChatCompletions(failures=99)
        result = AnswerGenerator(GPTClient(FakeChatClient(completions))).generate("问题", [create_item()])
    finally:
        settings.gpt_max_retries = original_retries
        settings.gpt_retry_delay_seconds = original_delay

    assert result.answer == GENERATION_FAILURE_ANSWER
    assert result.used_context is True
    assert len(completions.calls) == 2
