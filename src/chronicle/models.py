"""输入层数据模型。

这里只放「Git 说了什么」的事实，不放任何统计口径。
统计口径全部落在 features/ 里，方便单独改阈值而不动解析逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

__all__ = ["FileChange", "Commit"]


@dataclass(slots=True)
class FileChange:
    """一次提交里单个文件的行数变化。"""

    path: str
    insertions: int
    deletions: int
    binary: bool = False

    @property
    def churn(self) -> int:
        return self.insertions + self.deletions


@dataclass(slots=True)
class Commit:
    """一条提交。changes 已剔除噪音文件，dropped 是被判为批量导入的文件。"""

    sha: str
    parents: tuple[str, ...]
    author: str
    email: str
    authored_at: datetime
    committed_at: datetime
    subject: str
    changes: list[FileChange] = field(default_factory=list)
    dropped: list[FileChange] = field(default_factory=list)
    kind: str = "other"

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1

    @property
    def short_sha(self) -> str:
        return self.sha[:8]

    @property
    def insertions(self) -> int:
        return sum(c.insertions for c in self.changes)

    @property
    def deletions(self) -> int:
        return sum(c.deletions for c in self.changes)

    @property
    def churn(self) -> int:
        return self.insertions + self.deletions

    @property
    def files_changed(self) -> int:
        return len(self.changes)

    @property
    def dropped_churn(self) -> int:
        return sum(c.churn for c in self.dropped)

    @property
    def is_bulk(self) -> bool:
        """被判为数据集/生成物批量导入的提交。"""
        return bool(self.dropped)

    @property
    def is_code_commit(self) -> bool:
        """剔除噪音后仍有文件变化的提交，才算进代码量统计。"""
        return bool(self.changes)
