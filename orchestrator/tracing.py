"""Tool-call instrumentation.

mcp-use converts each MCP tool into a LangChain BaseTool subclass whose async
entry point is `_arun`, not the `.coroutine` attribute that plain
StructuredTool uses. We wrap `_arun` so every invocation records a ToolEvent.
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ToolEvent:
    id: str
    tool: str
    arguments: dict[str, Any]
    duration_ms: int
    ok: bool
    preview: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Trace:
    turn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    events: list[ToolEvent] = field(default_factory=list)

    def clear(self) -> None:
        self.events.clear()
        self.turn_id = uuid.uuid4().hex


def _event(tool_name, kwargs, started, ok, preview="", error=None) -> ToolEvent:
    return ToolEvent(
        id=uuid.uuid4().hex,
        tool=tool_name,
        arguments=kwargs,
        duration_ms=int((time.perf_counter() - started) * 1000),
        ok=ok,
        preview=preview,
        error=error,
    )


def instrument(tool, trace: Trace, on_event: Callable[[ToolEvent], Awaitable[None]]):
    """Wrap a LangChain BaseTool so each call appends a ToolEvent to `trace`.

    Returns the same tool object, patched in place.
    """
    # Each mcp-use tool gets its own dynamically created class, so patching the
    # class is safe and does not leak across tools.
    cls = type(tool)

    if getattr(cls, "_is_instrumented", False):
        return tool

    original = cls._arun

    async def wrapped(self, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await original(self, **kwargs)
        except Exception as exc:
            event = _event(self.name, kwargs, started, ok=False, error=str(exc))
            trace.events.append(event)
            await on_event(event)
            raise

        event = _event(self.name, kwargs, started, ok=True, preview=str(result)[:300])
        trace.events.append(event)
        await on_event(event)
        return result

    cls._arun = wrapped
    cls._is_instrumented = True
    return tool


class TraceSink:
    """A retargetable destination for tool events.

    instrument() binds a tool's class once. For a long-lived agent serving many
    requests (the gateway), each request needs its own trace and callback
    without re-patching the class. The tool is instrumented against a shared
    sink, and the gateway swaps the sink's target per request.
    """

    def __init__(self) -> None:
        self.trace = Trace()
        self._on_event: Callable[[ToolEvent], Awaitable[None]] = _noop

    def retarget(self, trace: Trace, on_event: Callable[[ToolEvent], Awaitable[None]]) -> None:
        self.trace = trace
        self._on_event = on_event

    async def emit(self, event: ToolEvent) -> None:
        self.trace.events.append(event)
        await self._on_event(event)


async def _noop(event: ToolEvent) -> None:
    return None


def instrument_to_sink(tool, sink: "TraceSink"):
    """Instrument a tool so its events flow to a retargetable sink.

    Unlike instrument(), which binds a fixed trace, this lets the caller change
    the destination per request by calling sink.retarget().
    """
    cls = type(tool)
    if getattr(cls, "_sink_instrumented", False):
        return tool

    original = cls._arun

    async def wrapped(self, **kwargs):
        started = time.perf_counter()
        try:
            result = await original(self, **kwargs)
        except Exception as exc:
            await sink.emit(_event(self.name, kwargs, started, ok=False, error=str(exc)))
            raise
        await sink.emit(_event(self.name, kwargs, started, ok=True, preview=str(result)[:300]))
        return result

    cls._arun = wrapped
    cls._sink_instrumented = True
    cls._sink = sink
    return tool
