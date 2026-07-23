import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable


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