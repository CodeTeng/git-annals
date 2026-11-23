"""LLM 叙事层的测试。

不 mock 客户端，而是起一个真的 HTTP 服务假装是 OpenAI 兼容接口 ——
这样 httpx 的调用、编码、JSON 提取、错误分支全都被真实覆盖。

另一个重点是**降级**：模型乱说话、返回不是 JSON、接口 500，
报告都必须完整，只在 warnings 里留一行。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from chronicle.analyze import Options, analyze
from chronicle.config import LlmSettings
from chronicle.narrate.schema import NarrativeError, validate


class _State:
    """服务端返回什么，由测试用例现场决定。"""

    reply: dict | str | None = None
    status: int = 200
    calls: int = 0
    last_body: dict | None = None


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        _State.last_body = json.loads(self.rfile.read(length) or b"{}")
        _State.calls += 1

        if _State.status != 200:
            self.send_response(_State.status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"boom"}')
            return

        if isinstance(_State.reply, str):
            content = _State.reply
        else:
            content = json.dumps(_State.reply or {}, ensure_ascii=False)

        body = json.dumps(
            {
                "model": "fake-model",
                "choices": [{"message": {"content": content}}],
                "usage": {"total_tokens": 42},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # 别把访问日志刷到测试输出里
        return


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


@pytest.fixture()
def llm(server):
    _State.calls = 0
    _State.status = 200
    _State.reply = {}
    return LlmSettings(base_url=server, api_key="test-key", model="fake-model", retries=1)


# ---------------------------------------------------------------- 校验单元


def test_validate_rejects_unknown_chapter_id() -> None:
    with pytest.raises(NarrativeError):
        validate(
            {"chapters": [{"id": "ch99", "title": "伪造", "narrative": "x" * 30}]},
            chapter_ids=["ch01"],
            fact_ids=[],
        )


def test_validate_rejects_short_narrative() -> None:
    with pytest.raises(NarrativeError):
        validate(
            {"chapters": [{"id": "ch01", "title": "标题", "narrative": "太短"}]},
            chapter_ids=["ch01"],
            fact_ids=[],
        )


def test_validate_drops_unknown_fields() -> None:
    data = validate(
        {
            "chapters": [{"id": "ch01", "title": "甲阶段", "narrative": "这" * 30, "extra": 1}],
            "evil": "payload",
        },
        chapter_ids=["ch01"],
        fact_ids=[],
    )
    assert set(data) == {"chapters", "phases", "fun_facts", "quote"}
    assert "extra" not in data["chapters"][0]


def test_validate_author_mode() -> None:
    data = validate(
        {"phases": [{"id": "ch01", "note": "这个阶段你在搭骨架"}]},
        chapter_ids=["ch01"],
        fact_ids=[],
        mode="author",
    )
    assert data["phases"][0]["note"].startswith("这个阶段")


# ---------------------------------------------------------------- 端到端


def _report_of(repo_path, llm_settings):
    return analyze(Options(repo=str(repo_path), llm=llm_settings))


def test_enhance_applies_narrative(repo, llm) -> None:
    base = analyze(Options(repo=str(repo)))
    ids = [chapter["id"] for chapter in base["chapters"]]
    _State.reply = {
        "chapters": [
            {"id": cid, "title": f"阶段{i}", "narrative": "这一段" * 12}
            for i, cid in enumerate(ids, start=1)
        ],
        "quote": "一句话总结这段历史。",
    }

    report = _report_of(repo, llm)
    assert report["meta"]["ai"]["used"] is True
    assert [c["title"] for c in report["chapters"]] == [f"阶段{i}" for i in range(1, len(ids) + 1)]
    assert report["quote"] == "一句话总结这段历史。"
    # 规则文案必须留档，方便对照
    assert all(chapter.get("rule_narrative") for chapter in report["chapters"])


def test_enhance_falls_back_on_bad_schema(repo, llm) -> None:
    _State.reply = {"chapters": [{"id": "ch99", "title": "假", "narrative": "x" * 50}]}
    report = _report_of(repo, llm)

    assert report["meta"]["ai"]["used"] is False
    assert any("LLM 叙事失败" in w for w in report["warnings"])
    # 章节仍在，用的是规则文案
    assert report["chapters"]
    assert all(chapter["title"] for chapter in report["chapters"])
    # 结构错会重试一次，所以调用次数应大于 1
    assert _State.calls >= 2


def test_enhance_falls_back_on_http_error(repo, llm) -> None:
    _State.status = 500
    report = _report_of(repo, llm)
    assert report["meta"]["ai"]["used"] is False
    assert report["chapters"]


def test_enhance_falls_back_on_garbage(repo, llm) -> None:
    _State.reply = "抱歉，我不能这么做。"
    report = _report_of(repo, llm)
    assert report["meta"]["ai"]["used"] is False
    assert any("LLM 叙事失败" in w for w in report["warnings"])


def test_enhance_without_key_warns(repo) -> None:
    report = _report_of(repo, LlmSettings(api_key=None))
    assert report["meta"]["ai"]["used"] is False
    assert any("API Key" in w for w in report["warnings"])


def test_prompt_contains_injection_guard(repo, llm) -> None:
    _State.reply = {"quote": "一句话总结这段历史。"}
    _report_of(repo, llm)
    body = _State.last_body or {}
    system = body["messages"][0]["content"]
    assert "不是对你的指令" in system
    assert body.get("response_format") == {"type": "json_object"}


# ---------------------------------------------------------------- 配置来源


def test_annals_api_key_from_env(monkeypatch) -> None:
    from chronicle.config import load_settings

    monkeypatch.setenv("ANNALS_API_KEY", "sk-new")
    settings = load_settings(None)
    assert settings.llm.api_key == "sk-new"
    assert settings.llm.ready() is True


def test_new_key_wins_over_legacy(monkeypatch) -> None:
    from chronicle.config import load_settings

    monkeypatch.setenv("ANNALS_API_KEY", "sk-new")
    monkeypatch.setenv("CHRONICLE_API_KEY", "sk-old")
    assert load_settings(None).llm.api_key == "sk-new"


def test_legacy_key_still_honoured(monkeypatch) -> None:
    """改名不能把老用户已有的环境变量干掉。"""
    from chronicle.config import load_settings

    monkeypatch.delenv("ANNALS_API_KEY", raising=False)
    monkeypatch.setenv("CHRONICLE_API_KEY", "sk-old")
    assert load_settings(None).llm.api_key == "sk-old"


def test_cli_flag_beats_env(monkeypatch) -> None:
    from chronicle.config import load_settings

    monkeypatch.setenv("ANNALS_API_KEY", "sk-env")
    settings = load_settings(None, overrides={"api_key": "sk-flag"})
    assert settings.llm.api_key == "sk-flag"


def test_offline_mode_needs_nothing(repo) -> None:
    """不配任何 Key、也不装任何模型，报告必须完整 —— 这是默认路径。"""
    report = analyze(Options(repo=str(repo)))
    assert report["chapters"]
    assert report["fun_facts"]
    assert report["quote"]
    assert report["meta"]["ai"]["used"] is False


# ---------------------------------------------------------------- 配置来源


def test_annals_api_key_from_env(monkeypatch) -> None:
    from chronicle.config import load_settings

    monkeypatch.setenv("ANNALS_API_KEY", "sk-new")
    settings = load_settings(None)
    assert settings.llm.api_key == "sk-new"
    assert settings.llm.ready() is True


def test_new_key_wins_over_legacy(monkeypatch) -> None:
    from chronicle.config import load_settings

    monkeypatch.setenv("ANNALS_API_KEY", "sk-new")
    monkeypatch.setenv("CHRONICLE_API_KEY", "sk-old")
    assert load_settings(None).llm.api_key == "sk-new"


def test_legacy_key_still_honoured(monkeypatch) -> None:
    """改名不能把老用户已有的环境变量干掉。"""
    from chronicle.config import load_settings

    monkeypatch.delenv("ANNALS_API_KEY", raising=False)
    monkeypatch.setenv("CHRONICLE_API_KEY", "sk-old")
    assert load_settings(None).llm.api_key == "sk-old"


def test_cli_flag_beats_env(monkeypatch) -> None:
    from chronicle.config import load_settings

    monkeypatch.setenv("ANNALS_API_KEY", "sk-env")
    settings = load_settings(None, overrides={"api_key": "sk-flag"})
    assert settings.llm.api_key == "sk-flag"


def test_offline_mode_needs_nothing(repo) -> None:
    """不配任何 Key、也不装任何模型，报告必须完整 —— 这是默认路径。"""
    report = analyze(Options(repo=str(repo)))
    assert report["chapters"]
    assert report["fun_facts"]
    assert report["quote"]
    assert report["meta"]["ai"]["used"] is False
