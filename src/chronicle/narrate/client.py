"""OpenAI 兼容的对话接口客户端。

刻意不用官方 SDK：兼容层只需要一个 POST，多一个依赖就多一层版本冲突。
DeepSeek、通义、Kimi、vLLM、Ollama 的 /v1 都能直连，换个 base_url 就行。
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ..config import LlmSettings

__all__ = ["LlmError", "ChatClient"]


class LlmError(RuntimeError):
    """网络失败、非 200、或返回内容不是合法 JSON。"""


class ChatClient:
    def __init__(self, settings: LlmSettings) -> None:
        self.settings = settings

    @property
    def endpoint(self) -> str:
        base = self.settings.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def complete_json(
        self, system: str, user: str, *, repair: str | None = None
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """发一次请求，返回 (解析后的 JSON, 元信息)。

        repair 不为空时，说明上一次输出不合格，把它一起发回去要求修正 ——
        比重新生成便宜，也更容易保住已经对的部分。
        """
        if not self.settings.api_key:
            raise LlmError("没有找到 API Key")

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if repair:
            messages.append(
                {"role": "user", "content": f"上一次输出不合格：{repair}\n请只输出修正后的 JSON。"}
            )

        body: dict[str, Any] = {
            "model": self.settings.model,
            "messages": messages,
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.settings.timeout) as client:
                response = client.post(self.endpoint, json=body, headers=headers)
                if response.status_code == 400 and "response_format" in response.text:
                    # 有些自建服务不认 json_object，去掉再试一次
                    body.pop("response_format", None)
                    response = client.post(self.endpoint, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise LlmError(f"请求失败：{exc}") from exc

        elapsed = int((time.perf_counter() - started) * 1000)
        if response.status_code != 200:
            snippet = response.text[:300].replace("\n", " ")
            raise LlmError(f"HTTP {response.status_code}：{snippet}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise LlmError(f"响应不是 JSON：{response.text[:200]}") from exc

        choices = payload.get("choices") or []
        if not choices:
            raise LlmError("响应里没有 choices")
        content = (choices[0].get("message") or {}).get("content") or ""

        data = _loads_loose(content)
        meta = {
            "model": payload.get("model") or self.settings.model,
            "latency_ms": elapsed,
            "usage": payload.get("usage") or {},
        }
        return data, meta


def _loads_loose(text: str) -> dict[str, Any]:
    """容忍模型把 JSON 包在 ```json 里，或者前后带一句客套话。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise LlmError(f"返回内容不是 JSON：{text[:200]}")
        try:
            data = json.loads(cleaned[start : end + 1])
        except ValueError as exc:
            raise LlmError(f"返回内容不是 JSON：{text[:200]}") from exc

    if not isinstance(data, dict):
        raise LlmError("返回的 JSON 顶层不是对象")
    return data
