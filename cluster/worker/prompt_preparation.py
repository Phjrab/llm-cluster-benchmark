"""Pinned text-only preparation; no evaluator exposed to the formatter."""
import hashlib
import inspect


class PreparationError(ValueError):
    """Safe reason code suitable for a public preparation failure."""


class _Captured(Exception):
    def __init__(self, kwargs):
        self.kwargs = kwargs


class _TokenizerOnly:
    def __init__(self, tokenize):
        self.tokenize = tokenize
        self.verbose = False

    def create_completion(self, **kwargs):
        raise _Captured(kwargs)


def capture_chat_input(llm, messages):
    # Import only on an explicitly invoked Worker preparation, never Controller.
    import llama_cpp
    from llama_cpp import llama_chat_format
    if llama_cpp.__version__ != "0.3.20":
        raise PreparationError("TOKENIZER_VERSION_UNVERIFIED")
    handler = (llm.chat_handler or llm._chat_handlers.get(llm.chat_format)
               or llama_chat_format.get_chat_completion_handler(llm.chat_format))
    if (getattr(handler, "__module__", "") != "llama_cpp.llama_chat_format"
            or not getattr(handler, "__qualname__", "").startswith("chat_formatter_to_chat_completion_handler.<locals>.")):
        raise PreparationError("CHAT_HANDLER_UNVERIFIED")
    formatter = inspect.getclosurevars(handler).nonlocals.get("chat_formatter")
    template = getattr(formatter, "template", None)
    if not isinstance(template, str) or not template:
        raise PreparationError("CHAT_TEMPLATE_IDENTITY_UNAVAILABLE")
    try:
        handler(llama=_TokenizerOnly(llm.tokenize), messages=messages,
                max_tokens=1, stream=False, repeat_penalty=1.0)
    except _Captured as captured:
        return captured.kwargs, hashlib.sha256(template.encode("utf-8")).hexdigest()
    raise PreparationError("EXACT_INPUT_UNAVAILABLE")
