"""Shared scaffolding for tests that drive the Anthropic SDK with scripted responses.

Used by test_notes, test_chat, and test_server.
"""


class FakeBlock:
    """Mimics an Anthropic SDK content block (text or tool_use)."""

    def __init__(self, type_, **kwargs):
        self.type = type_
        for k, v in kwargs.items():
            setattr(self, k, v)


def text_block(text):
    return FakeBlock("text", text=text)


def tool_use(name, input_, id_=None):
    return FakeBlock("tool_use", name=name, input=input_, id=id_ or f"tu_{name}")


class FakeResponse:
    def __init__(self, content_blocks):
        self.content = content_blocks
        self.stop_reason = "tool_use" if any(b.type == "tool_use" for b in content_blocks) else "end_turn"


class FakeMessages:
    """Returns a scripted sequence of FakeResponse objects, one per .create() call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessages.create called more times than scripted")
        return self._responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)
