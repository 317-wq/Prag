"""文档解析与分块共享数据结构。"""

from dataclasses import dataclass, field


@dataclass
class ParsedDocument:
    """单个解析片段，包含文本和位置信息。"""

    text: str
    metadata: dict[str, str | int | None] = field(default_factory=dict)


@dataclass
class ParseResult:
    """文档解析结果，包含片段、耗时和数量。"""

    documents: list[ParsedDocument]
    duration_ms: int

    @property
    def chunk_count(self) -> int:
        """返回解析出的片段数量。"""
        return len(self.documents)
