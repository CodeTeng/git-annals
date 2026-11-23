"""脱敏。

`--anon` 的承诺是「这份报告可以安全地发出去」。承诺要能兑现只有一条路：
**所有对外可见的字符串都必须从这里过一遍。**

覆盖四类：

  人     作者名 → 开发者 A / B / C；邮箱 → dev-a@redacted
  仓库   仓库名 → repo-<hash>；绝对路径与 remote → 置空
  文件   路径逐段哈希，保留通用目录名与扩展名（`src/p3f9a1/p2b8c4.py`）
  内容   commit subject 整体替换为类型标签，内容一个字都不留

入口只有一个：`analyze()` 在报告成形之后、交给 LLM 和渲染之前调用一次。
测试 `test_redaction` 会拿真实的姓名、邮箱、仓库名、标题去搜产物，命中即失败 ——
这条测试就是这份承诺的执行机制。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .i18n import t
from .models import Commit

__all__ = ["Redactor", "redact_report", "GENERIC_SEGMENTS"]

# 这些目录名不带身份信息，保留原样，否则路径会变成一串乱码，报告失去可读性
GENERIC_SEGMENTS = frozenset(
    {
        "src", "lib", "libs", "app", "apps", "pkg", "packages", "internal", "cmd",
        "test", "tests", "spec", "specs", "doc", "docs", "example", "examples",
        "config", "configs", "conf", "settings", "script", "scripts", "bin", "tools",
        "assets", "static", "public", "web", "frontend", "backend", "server", "client",
        "api", "core", "common", "shared", "utils", "util", "helpers", "helper",
        "model", "models", "view", "views", "component", "components", "page", "pages",
        "db", "migrations", "schema", "types", "hooks", "store", "services",
        "main", "index", "__init__", "module", "modules", "plugin", "plugins",
        "eval", "bench", "benchmark", "experiments", "notebooks", "paper", "papers",
        "data", "dataset", "datasets", "output", "outputs", "results",
        "main", "root", "tmp", "temp", "var", "etc", "opt", "usr", "home",
        "根目录",
    }
)

_QUOTED = re.compile(r"「([^」]{2,200})」|\"([^\"]{2,200})\"")
# 兜底：任何还长得像邮箱的东西（包括 `x***@corp.com` 这种半遮的）一律抹掉域名
_EMAILISH = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _hash(token: str, size: int = 6) -> str:
    return hashlib.sha1(token.encode("utf-8")).hexdigest()[:size]


class Redactor:
    def __init__(
        self,
        commits: list[Commit],
        *,
        repo_name: str = "",
        remote_url: str | None = None,
        lang: str = "zh",
        primary_email: str | None = None,
    ) -> None:
        self.lang = lang
        self._name_map: dict[str, str] = {}
        self._email_map: dict[str, str] = {}
        self._segment_map: dict[str, str] = {}
        self._subjects: set[str] = set()

        self.repo_name_source = repo_name
        self.repo_alias = f"repo-{_hash(repo_name)}" if repo_name else "repository"
        self.remote_url = remote_url
        self._build_identity(commits, primary_email)
        self._build_subjects(commits)
        self._build_replacer()

    # ---------------------------------------------------------------- 构建

    def _build_identity(self, commits: list[Commit], primary_email: str | None = None) -> None:
        """按「首次出现时间」发字母，保证同一个仓库每次跑出来别名一致。

        个人画像里被分析的那个人优先拿 A —— 报告的主角叫「开发者 B」很别扭。
        """
        ordered = sorted(commits, key=lambda c: c.authored_at)
        if primary_email:
            target = primary_email.strip().lower()
            ordered.sort(key=lambda c: (c.email or "").strip().lower() != target)
        for commit in ordered:
            email = (commit.email or "").strip().lower()
            name = (commit.author or "").strip()
            if email and email not in self._email_map:
                index = len(self._email_map)
                self._email_map[email] = f"dev-{_letters(index)}@redacted"
            if name and name not in self._name_map:
                alias = self._email_map.get(email)
                if alias:
                    dev = alias.split("@")[0].replace("dev-", "").upper()
                    self._name_map[name] = self._person_label(dev)
                else:
                    self._name_map[name] = self._person_label(_letters(len(self._name_map)))

    def _person_label(self, letter: str) -> str:
        return f"开发者 {letter}" if self.lang == "zh" else f"Author {letter}"

    def _build_subjects(self, commits: list[Commit]) -> None:
        raw = {c.subject.strip() for c in commits if c.subject.strip()}
        self._subjects = raw
        # 彩蛋里的标题被 _clip 截断过，带省略号，必须按前缀认
        self._subject_prefixes = {item[:47] for item in raw if len(item) > 4}

    def _build_replacer(self) -> None:
        tokens: set[str] = set(self._name_map) | set(self._email_map)
        if self.repo_name_source:
            tokens.add(self.repo_name_source)
        # 邮箱前缀也单独替换，避免 prose 里只出现 `teng2002823` 这类片段
        for email in list(self._email_map):
            local = email.split("@")[0]
            if len(local) >= 4:
                tokens.add(local)
        ordered = sorted(tokens, key=len, reverse=True)
        self._regex = (
            re.compile("|".join(re.escape(token) for token in ordered)) if ordered else None
        )

    # ---------------------------------------------------------------- 单值

    def person(self, value: str) -> str:
        value = (value or "").strip()
        return self._name_map.get(value, self._email_map.get(value.lower(), value))

    def email(self, value: str) -> str:
        value = (value or "").strip().lower()
        return self._email_map.get(value, f"dev-{_hash(value, 4)}@redacted")

    def segment(self, token: str) -> str:
        """路径里的单段。通用目录名原样保留，其余按哈希替换但保持稳定。"""
        if not token or token in GENERIC_SEGMENTS or token == "(root)":
            return token
        return self._segment_map.setdefault(token, f"p{_hash(token, 5)}")

    def path(self, value: str) -> str:
        if not value:
            return value
        parts = value.split("/")
        out = []
        for index, part in enumerate(parts):
            is_last = index == len(parts) - 1
            if is_last and "." in part.lstrip("."):
                stem, _, ext = part.rpartition(".")
                stem = self.segment(stem)
                out.append(f"{stem}.{ext}")
            else:
                out.append(self.segment(part))
        return "/".join(out)

    def branch(self, value: str) -> str:
        """分支名同样是内部信息：`feat/customer-x-migration` 一个名字就够泄密了。"""
        keep = {
            "main", "master", "develop", "dev", "release", "feat", "feature",
            "fix", "bugfix", "hotfix", "chore", "docs", "test", "tests", "exp",
        }
        parts = [part for part in (value or "").split("/") if part]
        if not parts:
            return value
        out = []
        for index, part in enumerate(parts):
            if index == 0 and part.lower() in keep:
                out.append(part)
            else:
                out.append(self.segment(part))
        return "/".join(out)

    def subject(self, kind: str) -> str:
        label = t(f"kind.{kind}", self.lang)
        if label == f"kind.{kind}":
            label = kind
        return f"【已脱敏·{label}】" if self.lang == "zh" else f"[redacted:{label}]"

    def text(self, value: str) -> str:
        """对一段散文做替换：人名、邮箱、仓库名，以及被引号括起来的原始标题。"""
        if not value:
            return value

        def _swap(match: re.Match) -> str:
            inner = (match.group(1) or match.group(2) or "").strip()
            body = inner.rstrip("…").strip()
            if body and (body in self._subjects or body in self._subject_prefixes):
                # 外面已经有引号了，这里只放标记，别套两层括号
                return "「已脱敏」" if self.lang == "zh" else '"[redacted]"'
            return match.group(0)

        value = _QUOTED.sub(_swap, value)
        value = _EMAILISH.sub(lambda m: self.email(m.group(0)), value)
        if self._regex is not None:
            value = self._regex.sub(lambda m: self._lookup(m.group(0)), value)
        for raw, alias in self._segment_map.items():
            if raw in value:
                value = value.replace(raw, alias)
        return value

    def _lookup(self, token: str) -> str:
        if token in self._name_map:
            return self._name_map[token]
        lowered = token.lower()
        if lowered in self._email_map:
            return self._email_map[lowered]
        if token == self.repo_name_source:
            return self.repo_alias
        for email, alias in self._email_map.items():
            if lowered == email.split("@")[0]:
                return alias
        return token

    # ---------------------------------------------------------------- 整棵树

    def apply(self, report: dict[str, Any]) -> dict[str, Any]:
        return _walk(report, self)


def _letters(index: int) -> str:
    if index < 26:
        return chr(ord("A") + index)
    return f"{_letters(index // 26 - 1)}{_letters(index % 26)}"


_PATH_KEYS = {"path"}
_SUBJECT_KEYS = {"subject"}
_NAME_KEYS = {"author"}
_EMAIL_KEYS = {"email", "key"}
_SKIP_KEYS = {"lang", "tool", "version", "schema_version", "generated_at", "python"}


def _walk(node: Any, redactor: Redactor, key: str = "") -> Any:
    if isinstance(node, dict):
        return {k: _walk(v, redactor, k) for k, v in node.items()}
    if isinstance(node, list):
        return [_walk(item, redactor, key) for item in node]
    if not isinstance(node, str):
        return node

    if key in _SKIP_KEYS:
        return node
    if key in _SUBJECT_KEYS:
        return redactor.subject("other")
    if key in _PATH_KEYS:
        return redactor.path(node)
    if key in _NAME_KEYS:
        return redactor.person(node)
    if key in _EMAIL_KEYS:
        return redactor.email(node)
    return redactor.text(node)


def redact_report(
    report: dict[str, Any],
    commits: list[Commit],
    *,
    primary_email: str | None = None,
) -> dict[str, Any]:
    """唯一入口。analyze() 在报告成形后调用一次。"""
    meta = report.get("meta") or {}
    repo = meta.get("repo") or {}
    redactor = Redactor(
        commits,
        repo_name=repo.get("name") or "",
        remote_url=repo.get("remote"),
        lang=meta.get("lang", "zh"),
        primary_email=primary_email,
    )

    # 结构化字段先处理掉，避免被通用文本替换改得不成形状
    for chapter in report.get("chapters") or []:
        if chapter.get("top_dirs"):
            chapter["top_dirs"] = [redactor.segment(name) for name in chapter["top_dirs"]]

    for row in (report.get("churn") or {}).get("dirs", []):
        row["name"] = redactor.segment(row.get("name", ""))

    persona = report.get("persona") or {}
    for item in persona.get("top_dirs") or []:
        item["name"] = redactor.segment(item.get("name", ""))

    for event in report.get("milestones") or []:
        if event.get("subject"):
            event["subject"] = redactor.subject(event.get("kind", "other"))

    report = _walk(report, redactor)

    # _walk 返回的是新树，这里必须重新取，否则改的是旧引用
    meta = report.get("meta") or {}
    repo = meta.get("repo") or {}
    if repo:
        # 绝对路径会带出系统用户名，remote 会带出组织，分支名会带出业务
        repo["name"] = redactor.repo_alias
        repo["root"] = "[redacted]"
        repo["remote"] = None
        if repo.get("branch"):
            repo["branch"] = redactor.branch(repo["branch"])
    meta["anon"] = True
    return report
