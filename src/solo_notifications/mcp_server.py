"""MCP server for the notifications cell: tools that call the cell's own HTTP API.

Every tool calls the API with the caller's own token, so MCP has exactly the authorization the API has: no tool
reads the database, and no tool accepts an actor, workspace, delegation or token argument. Unknown arguments are
rejected by strict pydantic models (``extra="forbid"``) before any HTTP call is made.

Configuration (same conventions as solo-cell-kit ``cellkit.mcp``)::

    EMPIRE_NOTIFICATIONS_API=https://...   env, else the owner-only ~/.config/empire-platform/client.env
    EMPIRE_TOKEN=...                bearer, else
    EMPIRE_TOKEN_FILE=/path/x.jwt   a token file (mode 600), else the automation token
    EMPIRE_MCP_AUTOMATION=owner-mcp EMPIRE_AUTOMATION_TOKEN_<NAME> or ~/.config/empire-platform/tokens/<name>.jwt
    EMPIRE_MCP_READONLY=1           register only the read tools

    notifications-mcp                        stdio server (see .mcp.json)
"""

from __future__ import annotations

import inspect
import os
import re
import urllib.parse
import uuid
from collections.abc import Callable
from datetime import date, datetime  # noqa: F401 - used by the tool models
from pathlib import Path
from typing import Any, Literal

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import Tool as MCPTool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolFailed(ToolError):
    """Reaches the MCP client as a tool error carrying only a fixed message mapped from the HTTP status."""


class ConfigError(RuntimeError):
    pass


# --- configuration (vendored from solo-cell-kit cellkit/mcp.py; do not import across repos) -----------


def client_file(environ: Any) -> dict[str, str]:
    override = (environ.get("EMPIRE_CLIENT_CONFIG") or "").strip()
    path = (
        Path(override).expanduser()
        if override
        else Path.home() / ".config" / "empire-platform" / "client.env"
    )
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return {}
    if mode & 0o077:
        raise ConfigError(
            f"refusing client config {path}: group/world accessible; run chmod 600"
        )
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("'\"")
    return values


def api_url(key: str, environ: Any = None) -> str:
    """The API base URL from ``key`` (env, else client.env): https only, http only on loopback."""
    environ = os.environ if environ is None else environ
    value = (environ.get(key) or "").strip() or client_file(environ).get(key, "")
    if not value:
        raise ConfigError(f"the API is not configured: set {key} (env or client.env)")
    parts = urllib.parse.urlsplit(value)
    host = (parts.hostname or "").lower()
    if (
        parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or "?" in value
        or "#" in value
    ):
        raise ConfigError(f"{key} must not carry credentials, a query or a fragment")
    if not (parts.scheme == "https" and host) and not (
        parts.scheme == "http" and host in LOOPBACK
    ):
        raise ConfigError(
            f"{key} must be https (http only for 127.0.0.1, ::1 or localhost)"
        )
    return value


def clean_token(value: str) -> str:
    """The token without surrounding whitespace; any inner whitespace or control character is refused."""
    token = value.strip()
    if not token or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in token):
        raise ConfigError(
            "the token has an unexpected format"
        ) from None  # never echo it
    return token


AUTOMATION = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
FORBIDDEN_ARGUMENTS = frozenset(
    {
        "actor",
        "actor_id",
        "workspace",
        "workspace_id",
        "tenant",
        "tenant_id",
        "delegation",
        "token",
        "authorization",
        "owner_id",
    }
)
IDEMPOTENCY_KEY = r"^[A-Za-z0-9._:-]{8,128}$"


def read_owner_file(path: Path, what: str) -> str:
    if path.stat().st_mode & 0o077:
        raise ConfigError(
            f"{what} file {path} must be readable by its owner only (chmod 600)"
        )
    return path.read_text(encoding="utf-8").strip()


# --- the server -----------------------------------------------------------------------------------


class StrictMCPServer(MCPServer):
    """MCPServer that validates arguments against a strict model before any tool runs."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.argument_models: dict[str, type[Strict]] = {}

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        for tool in tools:
            model = self.argument_models.get(tool.name)
            if model is not None:
                tool.input_schema = {
                    **model.model_json_schema(),
                    "additionalProperties": False,
                }
        return tools

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Any = None
    ) -> Any:
        model = self.argument_models.get(name)
        if model is None:
            raise ToolFailed("unknown_tool")
        try:
            model.model_validate(arguments or {})
            valid = True
        except ValidationError:
            valid = False  # raised below, outside the handler: no cause or context keeps the rejected input
        if not valid:
            raise ToolFailed(
                "invalid_arguments"
            ) from None  # never names the argument or echoes its value
        return await super().call_tool(name, arguments, context)


REASONS = {
    400: "invalid",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "invalid",
    409: "conflict",
    410: "not_found",
    413: "invalid",
    422: "invalid",
    429: "rate_limited",
}


def failure(status: int) -> str:
    """A fixed message from the status alone: upstream text and detail fields are never included."""
    return f"{status}: {'upstream_error' if status >= 500 else REASONS.get(status, 'error')}"


class Toolbox:
    """Registers tools on a strict server. Every tool is one HTTP call with the caller's own bearer."""

    def __init__(
        self,
        name: str,
        instructions: str,
        client: httpx.Client,
        token: Callable[[], str],
        *,
        read_only: bool = False,
        empire_headers: bool = False,
        server: StrictMCPServer | None = None,
    ) -> None:
        self.server = server or StrictMCPServer(name, instructions=instructions)
        self.client, self.token, self.read_only, self.empire_headers = (
            client,
            token,
            read_only,
            empire_headers,
        )

    def call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        idempotency_key: str | None = None,
    ) -> Any:
        try:
            bearer = clean_token(self.token())
        except ConfigError:
            raise ToolFailed(
                "401: unauthorized"
            ) from None  # no token, or one with an unexpected format
        headers = {"Authorization": f"Bearer {bearer}"}
        if self.empire_headers:
            headers |= {
                "X-Empire-Channel": "mcp",
                "X-Correlation-Id": f"mcp-{uuid.uuid4().hex}",
            }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = self.client.request(
                method, path, headers=headers, params=params, json=body
            )
        except httpx.HTTPError:
            raise ToolFailed(
                "503: unavailable"
            ) from None  # never echo the transport error
        if response.status_code >= 400:
            raise ToolFailed(failure(response.status_code))
        return response.json() if response.content else {"ok": True}

    def tool(
        self, name: str, model: type[Strict], description: str, *, write: bool = False
    ) -> Callable[[Callable[[Any], Any]], Callable[[Any], Any]]:
        def register(handler: Callable[[Any], Any]) -> Callable[[Any], Any]:
            banned = FORBIDDEN_ARGUMENTS & set(model.model_fields)
            if banned:
                raise ValueError(
                    f"{name}: identity arguments are not allowed: {sorted(banned)}"
                )
            if write and self.read_only:
                return handler

            def function(**kwargs: Any) -> Any:
                return handler(model.model_validate(kwargs))

            params = [
                inspect.Parameter(
                    field,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=inspect.Parameter.empty
                    if info.is_required()
                    else info.default,
                    annotation=info.annotation,
                )
                for field, info in model.model_fields.items()
            ]
            function.__signature__ = inspect.Signature(params)  # type: ignore[attr-defined]  # functions take a __signature__ at runtime; the server's inspect.signature() reads it
            function.__annotations__ = {p.name: p.annotation for p in params}
            function.__name__ = name.replace(".", "_")
            self.server.argument_models[name] = model
            self.server.tool(name=name, description=description)(function)
            return handler

        return register


def query(
    args: BaseModel, rename: dict[str, str] | None = None, drop: tuple[str, ...] = ()
) -> dict[str, Any]:
    """The set arguments as query parameters (``rename`` maps argument names to the API's names)."""
    values = args.model_dump(mode="json", exclude_none=True, exclude=set(drop))
    return {(rename or {}).get(key, key): value for key, value in values.items()}


def new_key() -> str:
    return f"mcp-{uuid.uuid4().hex}"


class Id(Strict):
    id: int = Field(gt=0)


API_KEY = "EMPIRE_NOTIFICATIONS_API"
RuleType = Literal[
    "overdue",
    "due_soon",
    "high_priority",
    "stale",
    "blocked",
    "status_change",
    "reminder",
]
Severity = Literal["info", "warning", "critical", "success"]
# Kinds the dispatcher never sends to a person (it delivers the task kinds, engagement_review and platform_check),
# so a note made through MCP stays in the log.
NoteKind = Literal["note", "agent_note", "status_change"]


class RuleList(Strict):
    active: bool | None = None
    rule_type: RuleType | None = None


class NotificationList(Strict):
    notification_type: list[str] | None = None
    since: datetime | None = None
    until: datetime | None = None
    task_id: int | None = None
    rule_id: int | None = Field(default=None, gt=0)
    undelivered: bool | None = None
    dedupe_key: str | None = Field(default=None, max_length=200)
    order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class NotificationStats(Strict):
    group_by: Literal["notification_type", "severity", "channel"] = "notification_type"
    since: datetime | None = None


class NotificationCreate(Strict):
    """A log entry on the console channel only; it is never delivered to a person."""

    title: str = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=8000)
    notification_type: NoteKind = "note"
    severity: Severity = "info"
    rule_id: int | None = Field(default=None, gt=0)
    task_id: int | None = None
    dedupe_key: str | None = Field(default=None, min_length=8, max_length=200)


class RuleCreate(Strict):
    """A new rule, always created inactive: turning a rule on is an owner action."""

    name: str = Field(min_length=1, max_length=200)
    rule_type: RuleType
    description: str | None = Field(default=None, max_length=4000)
    config: dict[str, Any] | None = None
    project_filter: str | None = Field(default=None, max_length=200)
    category_filter: str | None = Field(default=None, max_length=200)
    priority_filter: int | None = None
    status_filter: list[str] | None = None
    check_interval_minutes: int | None = Field(default=None, gt=0, le=100000)


def build_server(
    client: httpx.Client,
    token: Callable[[], str],
    *,
    read_only: bool = False,
    server: StrictMCPServer | None = None,
) -> Toolbox:
    box = Toolbox(
        "notifications",
        "Solo Empire notifications cell. Every tool calls the cell API as the "
        "authenticated caller only.",
        client,
        token,
        read_only=read_only,
        empire_headers=True,
        server=server,
    )

    @box.tool("notifications.rule.list", RuleList, "List notification rules.")
    def rule_list(a: RuleList) -> Any:
        return box.call("GET", "/v1/notification-rules", params=query(a))

    @box.tool("notifications.rule.get", Id, "Get one notification rule by id.")
    def rule_get(a: Id) -> Any:
        return box.call("GET", f"/v1/notification-rules/{a.id}")

    @box.tool(
        "notifications.notification.list",
        NotificationList,
        "List the notification log (newest first by default).",
    )
    def notification_list(a: NotificationList) -> Any:
        return box.call("GET", "/v1/notifications", params=query(a))

    @box.tool("notifications.notification.get", Id, "Get one notification by id.")
    def notification_get(a: Id) -> Any:
        return box.call("GET", f"/v1/notifications/{a.id}")

    @box.tool(
        "notifications.notification.stats",
        NotificationStats,
        "Count notifications by type, severity or channel.",
    )
    def notification_stats(a: NotificationStats) -> Any:
        return box.call("GET", "/v1/notifications/stats", params=query(a))

    @box.tool(
        "notifications.notification.create",
        NotificationCreate,
        "Add a console-only note to the notification log (idempotent on dedupe_key); nothing is sent to anyone.",
        write=True,
    )
    def notification_create(a: NotificationCreate) -> Any:
        return box.call(
            "POST",
            "/v1/notifications",
            body=a.model_dump(mode="json", exclude_none=True),
        )

    @box.tool(
        "notifications.rule.create",
        RuleCreate,
        "Create a notification rule, inactive; the owner turns rules on.",
        write=True,
    )
    def rule_create(a: RuleCreate) -> Any:
        return box.call(
            "POST",
            "/v1/notification-rules",
            body={**a.model_dump(mode="json", exclude_none=True), "is_active": False},
        )

    return box


def bearer(environ: Any = None) -> str:
    """The caller's own token: EMPIRE_TOKEN, EMPIRE_TOKEN_FILE, else the named automation token."""
    environ = os.environ if environ is None else environ
    if environ.get("EMPIRE_TOKEN"):
        return clean_token(environ["EMPIRE_TOKEN"])
    if environ.get("EMPIRE_TOKEN_FILE"):
        return clean_token(
            read_owner_file(Path(environ["EMPIRE_TOKEN_FILE"]).expanduser(), "token")
        )
    name = environ.get("EMPIRE_MCP_AUTOMATION") or "owner-mcp"
    if not AUTOMATION.fullmatch(name):
        raise ConfigError("EMPIRE_MCP_AUTOMATION must match ^[a-z0-9][a-z0-9-]{0,62}$")
    key = "EMPIRE_AUTOMATION_TOKEN_" + name.upper().replace("-", "_")
    if environ.get(key):
        return clean_token(environ[key])
    path = Path.home() / ".config" / "empire-platform" / "tokens" / f"{name}.jwt"
    if path.is_file():
        return clean_token(read_owner_file(path, "token"))
    raise ConfigError(f"no token: set EMPIRE_TOKEN, EMPIRE_TOKEN_FILE or {key}")


def main() -> None:
    try:
        url, _ = api_url(API_KEY), bearer()
    except ConfigError as exc:
        raise SystemExit(str(exc)) from None
    client = httpx.Client(base_url=url, timeout=30, follow_redirects=False)
    build_server(
        client, bearer, read_only=os.environ.get("EMPIRE_MCP_READONLY") == "1"
    ).server.run("stdio")


if __name__ == "__main__":
    main()
