"""LLM 叙事层：把确定性统计交给模型，换回一段人话。"""

from .client import ChatClient, LlmError
from .enhance import enhance
from .schema import NarrativeError

__all__ = ["ChatClient", "LlmError", "NarrativeError", "enhance"]
