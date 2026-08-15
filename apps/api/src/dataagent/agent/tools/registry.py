"""The gate every tool call goes through (architecture Part 4.6).

4.6 gives the registry five jobs and this module does exactly those: filter the
tool list per role **before the model sees it**, validate arguments against the
schema before dispatch, declare what each call costs a budget, emit the trace
events, and wrap every result in a typed envelope.

The ordering of the first two is the point. A tool the caller may not use is
never described to the model, so a hijacked prompt cannot ask for a capability it
was never offered — and if it names one anyway, the answer is the same
``unknown_tool`` a typo gets, because "exists but you may not have it" is a fact
worth not disclosing.

**Arguments are validated here, not in the handler.** A handler that had to check
its own arguments would be a handler that could forget, and the model's output is
the least trustworthy input in the system. Validation failure is repairable and
says what was wrong, because the next thing that happens is the model trying
again with one attempt (WP7.2b).

**Nothing here decides tenancy.** ``ToolContext`` carries the organization and
the handlers pass it down to the DAL and the catalog, which are org-scoped and
sit behind row-level security. The registry is a usability and budget boundary,
never a security one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import ValidationError

from dataagent.agent.tools.base import Tool, ToolContext, ToolError, ToolResult, UnknownToolError
from dataagent.auth.guards import ADMIN_ONLY, ANY_MEMBER, CONTRIBUTOR_OR_ADMIN
from dataagent.runs.events import EventWriter

__all__ = ["ToolRegistry", "default_registry"]

#: Which roles satisfy each declared minimum. Built from the guard module's own
#: tuples so the agent cannot drift from the API's answer about the same word.
_ROLES_SATISFYING: Mapping[str, tuple[str, ...]] = {
    "reader": ANY_MEMBER,
    "contributor": CONTRIBUTOR_OR_ADMIN,
    "admin": ADMIN_ONLY,
}


class ToolRegistry:
    """A set of tools, and the only way to call one."""

    __slots__ = ("_tools",)

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"a tool named {tool.name!r} is already registered")
        if tool.required_role not in _ROLES_SATISFYING:
            raise ValueError(
                f"{tool.name!r} requires role {tool.required_role!r}, which is not one of "
                f"{sorted(_ROLES_SATISFYING)}"
            )
        self._tools[tool.name] = tool

    def available_to(self, role: str) -> tuple[Tool, ...]:
        """The tools this role may call, in registration order.

        Registration order rather than alphabetical: the order tools appear in a
        prompt is a weak nudge about which to reach for first, and it should be a
        decision somebody made rather than an accident of naming.
        """
        return tuple(
            tool for tool in self._tools.values() if role in _ROLES_SATISFYING[tool.required_role]
        )

    def describe_for(self, role: str) -> str:
        """The tool list as it is rendered into a prompt."""
        tools = self.available_to(role)
        if not tools:
            return "No tools are available to you."
        return "You may call these tools:\n" + "\n".join(tool.describe() for tool in tools)

    async def call(
        self,
        context: ToolContext,
        name: str,
        arguments: Mapping[str, Any] | None = None,
        *,
        events: EventWriter | None = None,
    ) -> ToolResult:
        """Validate, dispatch, and come back with an envelope either way.

        Never raises for a tool's own failure — that is what ``ToolResult.ok`` is
        for. It does propagate anything the *platform* could not do, because a
        platform that cannot write an event has a problem the agent must not
        paper over.
        """
        tool = self._tools.get(name)
        if tool is None or context.role not in _ROLES_SATISFYING[tool.required_role]:
            available = tuple(t.name for t in self.available_to(context.role))
            return await self._finish(
                events, ToolResult.failed(name, UnknownToolError(name, available))
            )

        raw = dict(arguments or {})
        try:
            params = tool.params.model_validate(raw)
        except ValidationError as error:
            return await self._finish(
                events,
                ToolResult.failed(
                    name,
                    ToolError(
                        f"arguments are not valid for {name}: {_readable(error)}",
                        code="invalid_arguments",
                        repairable=True,
                    ),
                    safe_args=_safe(raw),
                ),
            )

        if events is not None:
            # Emitted before dispatch, so a call that hangs or crashes is still
            # visible in the trace as something that was attempted.
            await events.emit("tool_called", {"tool": name, "safe_args": _safe(raw)})

        try:
            data = await tool.handler(context, params)
        except ToolError as error:
            return await self._finish(events, ToolResult.failed(name, error, safe_args=_safe(raw)))
        return await self._finish(events, ToolResult.succeeded(name, data, safe_args=_safe(raw)))

    async def _finish(self, events: EventWriter | None, result: ToolResult) -> ToolResult:
        """Record a failure in the trace; successes are described by their own
        tool's event, which knows more than this layer does."""
        if events is not None and not result.ok:
            await events.emit(
                "error",
                {"category": result.code or "tool_failed", "safe_message": result.error or ""},
            )
        return result


def _safe(arguments: Mapping[str, Any]) -> dict[str, object]:
    """Arguments as an event payload may carry them.

    Values are stringified and capped. A tool argument is model-authored text —
    a table name, a question, a SQL statement — so it is safe to record in the
    tenant's own trace, but it is not safe to assume it is short.
    """
    trimmed: dict[str, object] = {}
    for key, value in arguments.items():
        text = value if isinstance(value, str | int | float | bool | type(None)) else str(value)
        if isinstance(text, str) and len(text) > 500:
            text = text[:500] + "…"
        trimmed[key] = text
    return trimmed


def _readable(error: ValidationError) -> str:
    """A validation failure a model can act on.

    pydantic's own rendering names types and urls; what the next attempt needs is
    which field and what was wrong with it.
    """
    parts = [
        f"{'.'.join(str(item) for item in problem['loc']) or '(root)'}: {problem['msg']}"
        for problem in error.errors()
    ]
    return "; ".join(parts[:5])


def default_registry() -> ToolRegistry:
    """The V1 tool set that WP7.2 needs, in the order a run tends to use them.

    Architecture 4.6 lists more — ``get_relationships``, ``search_knowledge``,
    ``stat_test``, ``create_chart_spec`` — and each arrives with the phase that
    can honestly implement it: knowledge in Phase 10, charts in Phase 11. A
    registered tool with a stub behind it would be worse than an absent one,
    because the model would call it and believe the answer.
    """
    from dataagent.agent.tools.catalog import DESCRIBE_TABLE, SEARCH_TABLES
    from dataagent.agent.tools.sql import RUN_SQL

    return ToolRegistry((SEARCH_TABLES, DESCRIBE_TABLE, RUN_SQL))
