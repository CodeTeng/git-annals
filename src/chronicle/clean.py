"""噪音清洗与提交分类。

两件事，都被后续统计依赖：
1. 剔掉构建产物、依赖目录、锁文件、二进制资源 —— 否则 node_modules 里一次
   npm install 就能把「代码量波动」画成一座假山。
2. 给提交打类型标签（feat / fix / refactor …），churn 风险评分要用。
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Iterable, Sequence

from .models import Commit, FileChange

__all__ = [
    "DEFAULT_IGNORE",
    "BULK_LINE_THRESHOLD",
    "filter_noise",
    "is_noise",
    "file_category",
    "classify",
    "KINDS",
    "normalize_author_key",
]

# 单文件单次变更超过这个行数，一律视为数据/生成物导入，不计入代码量。
# 20k 是经验值：手写代码极少在单个文件里一次落两万行（大模块总是拆文件的），
# 而语料、数据集、序列化产物动辄几十万行。
BULK_LINE_THRESHOLD = 20_000

KINDS = (
    "merge",
    "revert",
    "feat",
    "fix",
    "refactor",
    "perf",
    "test",
    "docs",
    "build",
    "style",
    "chore",
    "other",
)

# 目录类噪音：任何一层路径命中即排除
_NOISE_DIRS = (
    "node_modules",
    "bower_components",
    "vendor",
    "third_party",
    "datasets",
    "dist",
    "build",
    "target",
    "out",
    "output",
    "coverage",
    "htmlcov",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".next",
    ".nuxt",
    ".cache",
    ".idea",
    ".vscode",
    ".gradle",
    ".tox",
    "site-packages",
    ".terraform",
    "Pods",
    "DerivedData",
)

# 文件名类噪音：命中 basename 即排除
_NOISE_FILES = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "npm-shrinkwrap.json",
    "poetry.lock",
    "Pipfile.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
    "Gemfile.lock",
    "bun.lockb",
    "uv.lock",
    "gradle.lockfile",
    ".DS_Store",
    "Thumbs.db",
)

# 通配类噪音
_NOISE_GLOBS = (
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.lock",
    "*.log",
    "*.snap",
    "*.bak",
    "*.orig",
    "*.rej",
    "*.swp",
    "*.tmp",
    "*~",
    "*.pyc",
    "*.pyo",
    "*.class",
    "*.jar",
    "*.war",
    "*.so",
    "*.dylib",
    "*.dll",
    "*.exe",
    "*.o",
    "*.a",
    "*.whl",
    "*.egg-info/*",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.webp",
    "*.ico",
    "*.bmp",
    "*.pdf",
    "*.psd",
    "*.zip",
    "*.tar",
    "*.gz",
    "*.7z",
    "*.mp4",
    "*.mov",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.eot",
    # 数据集 / 模型权重 / 序列化文件：这些一提交就是几十万行，会把代码量统计冲垮
    "*.jsonl",
    "*.ndjson",
    "*.parquet",
    "*.feather",
    "*.arrow",
    "*.pkl",
    "*.pickle",
    "*.npy",
    "*.npz",
    "*.h5",
    "*.hdf5",
    "*.onnx",
    "*.pt",
    "*.pth",
    "*.ckpt",
    "*.safetensors",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.mdb",
    "*.avro",
    "*.orc",
)

DEFAULT_IGNORE: tuple[str, ...] = _NOISE_DIRS + _NOISE_FILES + _NOISE_GLOBS

_DIR_SET = frozenset(_NOISE_DIRS)
_FILE_SET = frozenset(_NOISE_FILES)
_GLOB_SET = _NOISE_GLOBS


def is_noise(path: str, extra: Sequence[str] | None = None) -> bool:
    """判断一个仓库内相对路径是否属于噪音。

    extra 支持三种写法：`dir/`（目录段匹配）、`a/b.py`（完整路径 glob）、`*.xxx`（文件名 glob）。
    """
    segments = path.split("/")
    basename = segments[-1]

    if any(seg in _DIR_SET for seg in segments[:-1]):
        return True
    if basename in _FILE_SET:
        return True
    if any(fnmatch.fnmatchcase(basename, pattern) for pattern in _GLOB_SET):
        return True

    for pattern in extra or ():
        if pattern.endswith("/"):
            if pattern.rstrip("/") in segments[:-1]:
                return True
        elif "/" in pattern:
            if fnmatch.fnmatchcase(path, pattern):
                return True
        elif fnmatch.fnmatchcase(basename, pattern):
            return True
    return False


def filter_noise(commits: Iterable[Commit], extra: Sequence[str] | None = None) -> list[Commit]:
    """剔除噪音文件、隔离批量导入、打上类型标签，返回新列表。

    三道闸门，从确定到启发式：
    1. 路径规则命中 -> 直接丢（node_modules、锁文件、二进制）。
    2. 单文件单次变更超过 BULK_LINE_THRESHOLD -> 放进 dropped，不计入代码量。
       这一条挡的是「把 1.1M 行的数据集提交进仓库」这类事故，路径规则挡不住。
    3. 剩下的是真正的代码改动。
    """
    kept: list[Commit] = []
    # 同一个路径会出现几百次：vite 的 9951 条提交里有 4.4 万个文件改动，
    # 但不同路径只有几千个。逐个跑 168 条 glob 是白烧 8 秒。
    noise_cache: dict[str, bool] = {}

    def _is_noise(path: str) -> bool:
        cached = noise_cache.get(path)
        if cached is None:
            cached = is_noise(path, extra)
            noise_cache[path] = cached
        return cached

    for commit in commits:
        normal: list[FileChange] = []
        dropped: list[FileChange] = []
        for change in commit.changes:
            if _is_noise(change.path):
                continue
            if change.churn >= BULK_LINE_THRESHOLD:
                dropped.append(change)
            else:
                normal.append(change)
        commit.changes = normal
        commit.dropped = dropped
        commit.kind = classify(commit.subject, is_merge=commit.is_merge)
        kept.append(commit)
    return kept


_CODE_EXTS = frozenset(
    {
        ".py", ".pyi", ".java", ".kt", ".scala", ".go", ".rs", ".c", ".h", ".cc",
        ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".m", ".js", ".jsx",
        ".mjs", ".ts", ".tsx", ".vue", ".svelte", ".sh", ".bash", ".ps1", ".sql",
        ".lua", ".pl", ".r", ".jl", ".dart", ".ex", ".exs", ".clj", ".hs", ".ml",
    }
)
_DOC_EXTS = frozenset({".md", ".rst", ".txt", ".tex", ".adoc"})
_CONFIG_EXTS = frozenset({".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".env"})


def file_category(path: str) -> str:
    """code / doc / config / other。churn 评分要用它调权。"""
    name = path.rsplit("/", 1)[-1].lower()
    if name.startswith(".") or name in ("dockerfile", "makefile", "jenkinsfile", "procfile"):
        return "config"
    dot = name.rfind(".")
    ext = name[dot:] if dot > 0 else ""
    if ext in _CODE_EXTS:
        return "code"
    if ext in _DOC_EXTS:
        return "doc"
    if ext in _CONFIG_EXTS:
        return "config"
    return "other"


_CONVENTIONAL = re.compile(r"^(\w+)(?:\([^)]*\))?!?:\s", re.UNICODE)
_REVERT = re.compile(r"^(revert|rollback)\b|\brevert\b|回滚|撤销", re.IGNORECASE)

# 关键词按「优先级从高到低」排，先命中先返回
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("revert", ("revert", "rollback", "回滚", "撤销")),
    ("perf", ("perf", "performance", "optimize", "optimi", "speed up", "性能", "优化")),
    ("test", ("test", "spec", "coverage", "测试", "单测", "用例")),
    ("docs", ("doc", "docs", "readme", "comment", "typo", "文档", "注释", "说明")),
    ("build", ("build", "ci", "cd", "deploy", "release", "bump", "deps", "dependency",
               "dependencies", "docker", "构建", "打包", "发布", "部署", "依赖")),
    ("style", ("style", "format", "lint", "prettier", "eslint", "black", "ruff",
               "格式", "排版", "格式化")),
    ("refactor", ("refactor", "rename", "restructure", "extract", "move", "cleanup",
                  "clean up", "simplify", "重构", "重命名", "整理", "拆分", "抽取", "简化")),
    ("fix", ("fix", "bug", "hotfix", "patch", "issue", "error", "crash", "broken",
             "修复", "修", "错误", "异常", "崩溃", "补丁")),
    ("feat", ("feat", "feature", "add", "implement", "support", "introduce", "create",
              "new", "新增", "添加", "实现", "支持", "增加")),
    ("chore", ("chore", "misc", "tweak", "update", "update", "调整", "杂项")),
)


def classify(subject: str, *, is_merge: bool = False) -> str:
    """把提交信息归类到 KINDS 之一。

    先看 Conventional Commits 前缀，再退化为关键词匹配 —— 因为一大半真实仓库
    （尤其国内团队）根本不写 `feat:` 前缀。
    """
    if is_merge or subject.startswith("Merge "):
        return "merge"

    text = subject.strip()
    if not text:
        return "other"

    match = _CONVENTIONAL.match(text)
    if match:
        head = match.group(1).lower()
        if head in KINDS:
            return head
        if head in ("feature",):
            return "feat"
        if head in ("bugfix", "hotfix"):
            return "fix"
        if head in ("docs", "doc"):
            return "docs"
        if head in ("tests", "testing"):
            return "test"

    if _REVERT.search(text):
        return "revert"

    lowered = text.lower()
    for kind, words in _KEYWORDS:
        for word in words:
            if word in lowered:
                return kind
    return "other"


_BOT_NAMES = frozenset(
    {
        "dependabot", "renovate", "github-actions", "allcontributors", "imgbot",
        "pre-commit-ci", "codecov", "sourcery-ai", "mergify", "greenkeeper",
        "travis-ci", "coveralls", "cypress", "netlify", "vercel", "deepsource-io",
    }
)


def is_bot(name: str, email: str) -> bool:
    """机器人账号。

    真实仓库里 dependabot / allcontributors 能占几百次提交，
    把它们和真人并列在「谁在写」里，数字就是错的。
    """
    lowered_name = (name or "").strip().lower()
    lowered_email = (email or "").strip().lower()
    if "[bot]" in lowered_name or lowered_name.endswith(" bot") or lowered_name.endswith("-bot"):
        return True
    if "bot@" in lowered_email and lowered_email.split("@")[0].endswith("[bot]"):
        return True
    return lowered_name.split("[")[0] in _BOT_NAMES


def normalize_author_key(name: str, email: str) -> str:
    """作者身份归一。

    以邮箱为准：同一个人换过机器名、写过中文名和英文名，靠 name 合并会炸。
    邮箱缺失时退回 name。
    """
    key = (email or "").strip().lower()
    if key:
        return key
    return (name or "").strip().lower()
