"""Adapters over the OpenAI Python SDK (extra ``hone-select[openai]``); also for OpenAI-compatible servers
such as Ollama's ``/v1`` endpoint.

    from openai import OpenAI
    from hone_select.adapters.openai import OpenAIDecisionClient
    judge = OpenAIDecisionClient(OpenAI(), model="gpt-4.1-mini")
"""

from __future__ import annotations

import base64
import mimetypes
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from hone_select.adapters.emulated import EmulatedDecisionClient, with_parsed
from hone_select.ports import TextResult, TraceContext

PARAMS = ("temperature", "top_p", "seed", "max_tokens")  # passed through; other params are ignored


def to_openai_content(content: str | Sequence[Mapping[str, Any]]) -> Any:
    """TextClient content parts (design/current.md §7.3) -> OpenAI parts; images become base64 data URLs."""
    if isinstance(content, str):
        return content
    parts: list[dict[str, Any]] = []
    for part in content:
        if part.get("type") != "image":
            parts.append({"type": "text", "text": str(part.get("text", ""))})
            continue
        if "path" in part:
            mime = mimetypes.guess_type(str(part["path"]))[0] or "image/png"
            data = base64.b64encode(Path(part["path"]).read_bytes()).decode()
        else:
            mime, data = str(part.get("mime", "image/png")), str(part["data_b64"])
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
    return parts


class OpenAITextClient:
    """A ``TextClient`` over ``client.chat.completions.create``.

    ``params`` (temperature, top_p, seed, max_tokens) are defaults; per-call params override them and
    ``None`` leaves a param out of the request. Transport
    errors raise the SDK's own exceptions; a reply that is not valid JSON for a ``schema`` sets ``error``.
    """

    def __init__(self, client: Any, model: str, **params: Any) -> None:
        self.client = client
        self.model = model
        self.params = params

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        schema: Mapping[str, Any] | None = None,
        trace: TraceContext | None = None,
        **params: Any,
    ) -> TextResult:
        chosen = {k: v for k, v in {**self.params, **params}.items() if k in PARAMS and v is not None}
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m["role"], "content": to_openai_content(m["content"])} for m in messages],
            **chosen,
        }
        if schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "reply", "schema": dict(schema)},
            }
        response = self.client.chat.completions.create(**request)
        choice = response.choices[0]
        text = choice.message.content or ""
        usage = response.usage
        result = TextResult(
            text=text,
            model=str(response.model or self.model),
            finish_reason=choice.finish_reason,
            usage={"input_tokens": usage.prompt_tokens, "output_tokens": usage.completion_tokens}
            if usage
            else {},
        )
        return with_parsed(result, schema)


class OpenAIDecisionClient(EmulatedDecisionClient):
    """A ``DecisionClient`` over an OpenAI (or compatible) chat model: questions answered as JSON."""

    def __init__(self, client: Any, model: str, **params: Any) -> None:
        params = {"temperature": 0, **params}  # pass temperature=None for models that reject it
        super().__init__(OpenAITextClient(client, model, **params), model_id=model)
