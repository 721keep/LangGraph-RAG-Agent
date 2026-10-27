"""DeepSeek Chat Completions compatibility for pinned langchain-openai 0.3.16.

Preserve provider reasoning as message metadata, never as display content.
No thinking toggle is set here: callers retain their explicit/provider defaults.
"""
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI


def _preserve_reasoning(message, raw):
    value = raw.get("reasoning_content")
    if value is not None:
        if not isinstance(value, str):
            raise ValueError("DeepSeek reasoning_content must be a string or null.")
        message.additional_kwargs["reasoning_content"] = value


class DeepSeekChatOpenAI(ChatOpenAI):
    """Keep reasoning across non-streaming, streaming, checkpoint and tool turns."""

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        raw = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, raw["choices"]):
            if isinstance(generation.message, AIMessage):
                _preserve_reasoning(generation.message, choice["message"])
        return result

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        result = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if result is not None and isinstance(result.message, AIMessageChunk) and choices:
            _preserve_reasoning(result.message, choices[0].get("delta") or {})
        return result

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        # ChatOpenAI renames this to the OpenAI-specific spelling; DeepSeek uses max_tokens.
        if "max_completion_tokens" in payload:
            payload["max_tokens"] = payload.pop("max_completion_tokens")
        extra = payload.get("extra_body") or {}
        thinking = extra.get("thinking", {}).get("type")
        thinking_enabled = thinking != "disabled" and payload.get("reasoning_effort") != "none"
        if thinking_enabled and payload.get("tools") and payload.get("tool_choice") not in (None, "auto", "none"):
            raise ValueError("DeepSeek thinking does not support forced tool_choice.")
        messages = self._convert_input(input_).to_messages()
        for message, outgoing in zip(messages, payload.get("messages", [])):
            if not isinstance(message, AIMessage):
                continue
            value = message.additional_kwargs.get("reasoning_content")
            if value is not None:
                if not isinstance(value, str):
                    raise ValueError("DeepSeek reasoning_content must be a string or null.")
                outgoing["reasoning_content"] = value
            elif thinking_enabled and payload.get("tools"):
                # Never invent reasoning for a pre-fix or non-thinking checkpoint.
                raise ValueError("DeepSeek thinking tool history is missing reasoning_content; use a new thread or explicitly disable thinking.")
            if outgoing.get("tool_calls") and outgoing.get("content") is None:
                outgoing["content"] = ""
        return payload
