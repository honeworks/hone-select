"""Adapters over a LangChain chat model (extra ``hone-select[langchain]``).

from langchain_openai import ChatOpenAI
from hone_select.adapters.langchain import LangChainDecisionClient
judge = LangChainDecisionClient(ChatOpenAI(model="gpt-4.1-mini", temperature=0))
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from hone_select.adapters.emulated import EmulatedDecisionClient, with_parsed
from hone_select.adapters.openai import to_openai_content
from hone_select.ports import TextResult, TraceContext


class LangChainTextClient:
    """A ``TextClient`` over ``chat_model.invoke(messages)``.

    Configure temperature etc. on the chat model itself; per-call ``params`` are ignored. With a
    ``schema`` the model is asked for JSON matching it and the reply is parsed.
    """

    def __init__(self, chat_model: Any) -> None:
        self.chat_model = chat_model
        self.model = str(getattr(chat_model, "model_name", "") or getattr(chat_model, "model", "") or "")

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        schema: Mapping[str, Any] | None = None,
        trace: TraceContext | None = None,
        **params: Any,
    ) -> TextResult:
        converted = [{"role": m["role"], "content": to_openai_content(m["content"])} for m in messages]
        if schema is not None:
            instruction = f"Reply with a JSON object only, matching this JSON schema:\n{json.dumps(schema)}"
            converted.insert(0, {"role": "system", "content": instruction})
        reply = self.chat_model.invoke(converted)
        content = reply.content
        parts = [content] if isinstance(content, str) else content
        text = "".join(p if isinstance(p, str) else str(p.get("text", "")) for p in parts)
        usage: dict[str, Any] = dict(getattr(reply, "usage_metadata", None) or {})
        metadata: dict[str, Any] = dict(getattr(reply, "response_metadata", None) or {})
        result = TextResult(
            text=text,
            model=self.model,
            finish_reason=metadata.get("finish_reason"),
            usage={k: usage[k] for k in ("input_tokens", "output_tokens") if k in usage},
        )
        return with_parsed(result, schema)


class LangChainDecisionClient(EmulatedDecisionClient):
    """A ``DecisionClient`` over a LangChain chat model: questions answered as JSON."""

    def __init__(self, chat_model: Any) -> None:
        super().__init__(LangChainTextClient(chat_model))
