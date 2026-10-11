"""文本清洗与分块测试。"""

from uuid import uuid4

import pytest

from app.parsers.schemas import ParsedDocument
from app.parsers.text_chunker import TextChunker


def test_clean_text_removes_useless_spaces_and_duplicate_blank_lines():
    """清洗文本时压缩空白并保留段落边界。"""
    chunker = TextChunker(max_length=100, overlap=0)

    cleaned = chunker.clean_text(" 第一段\t内容  \n\n\n  第二段　内容 ")

    assert cleaned == "第一段 内容\n\n第二段 内容"


def test_chunk_preserves_title_section_page_and_parent_content():
    """分块结果保留标题、章节、页码边界并写入 parent_content。"""
    document_id = uuid4()
    parsed = ParsedDocument(
        text="第一段内容\n\n第二段内容",
        metadata={"title": "标题", "section": "章节", "page": 3},
    )

    result = TextChunker(max_length=100, overlap=0).chunk(document_id, [parsed])

    assert result.chunk_count == 1
    assert result.chunks[0].document_id == document_id
    assert result.chunks[0].chunk_index == 0
    assert result.chunks[0].parent_content == "标题：标题 | 章节：章节 | 页码：3"
    assert result.chunks[0].metadata["section"] == "章节"


def test_chunk_prefers_paragraph_split_before_length_split():
    """分块优先按段落切分，段落无法合并时再生成新 chunk。"""
    document_id = uuid4()
    parsed = ParsedDocument(text="第一段内容\n\n第二段内容\n\n第三段内容", metadata={"title": "标题"})

    result = TextChunker(max_length=12, overlap=0).chunk(document_id, [parsed])

    assert result.chunks[0].content == "第一段内容\n\n第二段内容"
    assert result.chunks[1].content == "第三段内容"


def test_long_paragraph_uses_configured_overlap():
    """超长段落按最大长度切分，并使用配置的 overlap。"""
    document_id = uuid4()
    parsed = ParsedDocument(text="abcdefghij12345", metadata={"title": "标题"})

    result = TextChunker(max_length=10, overlap=3).chunk(document_id, [parsed])

    assert [chunk.content for chunk in result.chunks] == ["abcdefghij", "hij12345"]


def test_invalid_chunk_config_is_rejected():
    """无效分块配置会明确报错。"""
    with pytest.raises(ValueError):
        TextChunker(max_length=10, overlap=10)
