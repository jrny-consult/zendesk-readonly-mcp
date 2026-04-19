#!/usr/bin/env python3
"""
Read-only Zendesk MCP Server for AI Worldwide analysis.

Provides tools for querying tickets, users, organizations, groups, views,
triggers, macros, and ticket fields via the Zendesk REST API.

Authentication via environment variables:
  ZENDESK_SUBDOMAIN  — e.g. "your-subdomain"
  ZENDESK_EMAIL      — e.g. "admin@example.com"
  ZENDESK_API_TOKEN  — API token from Zendesk Admin > Apps & Integrations > APIs
"""

import base64
import json
import os
import sys
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Server init
# ---------------------------------------------------------------------------

mcp = FastMCP("zendesk_mcp")

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

SUBDOMAIN = os.environ.get("ZENDESK_SUBDOMAIN", "")
EMAIL = os.environ.get("ZENDESK_EMAIL", "")
API_TOKEN = os.environ.get("ZENDESK_API_TOKEN", "")

if not all([SUBDOMAIN, EMAIL, API_TOKEN]):
    print(
        "ERROR: Missing required environment variables. "
        "Set ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, and ZENDESK_API_TOKEN.",
        file=sys.stderr,
    )

BASE_URL = f"https://{SUBDOMAIN}.zendesk.com/api/v2"

_auth_header: str = ""
if EMAIL and API_TOKEN:
    _creds = base64.b64encode(f"{EMAIL}/token:{API_TOKEN}".encode()).decode()
    _auth_header = f"Basic {_creds}"

HEADERS = {
    "Authorization": _auth_header,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------


async def _get(endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Make a read-only GET request to the Zendesk API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{BASE_URL}/{endpoint.lstrip('/')}",
            headers=HEADERS,
            params=params or {},
        )
        response.raise_for_status()
        return response.json()


def _handle_error(e: Exception) -> str:
    """Return a clear, actionable error message."""
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 401:
            return "Error: Authentication failed. Check ZENDESK_EMAIL and ZENDESK_API_TOKEN."
        if status == 403:
            return "Error: Permission denied. This API token may not have access to this resource."
        if status == 404:
            return "Error: Resource not found. Check the ID or subdomain."
        if status == 429:
            return "Error: Rate limit exceeded. Wait a moment and try again."
        return f"Error: Zendesk API returned HTTP {status}."
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. Check your network connection."
    return f"Error: {type(e).__name__}: {e}"


def _fmt_datetime(dt: Optional[str]) -> str:
    """Strip the T/Z for cleaner display."""
    if not dt:
        return "—"
    return dt.replace("T", " ").replace("Z", " UTC")


# ---------------------------------------------------------------------------
# Shared Pydantic models
# ---------------------------------------------------------------------------


class ResponseFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"


class PaginationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    limit: Optional[int] = Field(default=25, ge=1, le=100, description="Max results to return (1–100, default 25)")
    page: Optional[int] = Field(default=1, ge=1, description="Page number (default 1)")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")


class CustomEndpointInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    endpoint: str = Field(
        ...,
        min_length=3,
        max_length=250,
        description=(
            "Relative Zendesk API v2 GET endpoint, such as 'brands.json', "
            "'schedules.json', or 'help_center/articles.json'. Do not include a full URL."
        ),
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional query parameters for the GET request.",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.JSON, description="'markdown' or 'json'")

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        endpoint = v.strip().lstrip("/")
        if endpoint.startswith(("http://", "https://")):
            raise ValueError("Endpoint must be relative, not a full URL.")
        if endpoint.startswith("api/v2/"):
            endpoint = endpoint.removeprefix("api/v2/")
        if ".." in endpoint or "?" in endpoint or "#" in endpoint:
            raise ValueError("Endpoint must not contain path traversal, query strings, or fragments.")
        if not endpoint.endswith(".json"):
            raise ValueError("Endpoint must be a Zendesk JSON endpoint ending in .json.")
        return endpoint


# ---------------------------------------------------------------------------
# Tools: Custom GET endpoint
# ---------------------------------------------------------------------------


@mcp.tool(
    name="zendesk_get_endpoint",
    annotations={
        "title": "Read a Zendesk API Endpoint",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_get_endpoint(params: CustomEndpointInput) -> str:
    """
    Call an additional Zendesk API v2 GET endpoint without adding a new typed tool.

    This is the extension point for endpoints that are not yet modeled as a
    first-class MCP tool. It only accepts relative .json endpoints and only makes
    GET requests through the shared read-only _get helper.

    Args:
        params (CustomEndpointInput):
            - endpoint (str): Relative Zendesk API v2 endpoint, e.g. 'brands.json'
            - params (dict): Optional query parameters
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Raw endpoint response as JSON, or a markdown-wrapped JSON payload.
    """
    try:
        data = await _get(params.endpoint, params=params.params)

        payload = {
            "endpoint": params.endpoint,
            "params": params.params,
            "data": data,
        }

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(payload, indent=2, default=str)

        return "\n".join(
            [
                f"## Zendesk Endpoint: `{params.endpoint}`",
                "",
                "```json",
                json.dumps(payload, indent=2, default=str),
                "```",
            ]
        )
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tools: Unified Search
# ---------------------------------------------------------------------------


class SearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    query: str = Field(
        ...,
        min_length=2,
        max_length=500,
        description=(
            "Zendesk search query. Supports type filters: 'type:ticket', 'type:user', 'type:organization'. "
            "Examples: 'type:ticket status:open', 'type:user email:@aiworldwide.com', "
            "'type:ticket tag:rma created>2025-01-01'"
        ),
    )
    limit: Optional[int] = Field(default=25, ge=1, le=100, description="Max results (1–100, default 25)")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Query cannot be empty.")
        return v.strip()


@mcp.tool(
    name="zendesk_search",
    annotations={
        "title": "Search Zendesk",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_search(params: SearchInput) -> str:
    """
    Search Zendesk for tickets, users, or organizations using the unified search API.

    Supports full Zendesk search syntax including field filters, date ranges,
    status, tags, assignees, and boolean operators.

    Args:
        params (SearchInput):
            - query (str): Zendesk search query (e.g. 'type:ticket status:open tag:rma')
            - limit (int): Max results to return (1–100, default 25)
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Formatted search results including count, type, and key fields per result.

    Examples:
        - 'type:ticket status:open' — all open tickets
        - 'type:ticket tag:rma' — tickets tagged rma
        - 'type:user email:@aiworldwide.com' — all AI Worldwide users
        - 'type:ticket created>2026-01-01 created<2026-04-01' — Q1 2026 tickets
        - 'type:ticket group_id:12345' — tickets in a specific group
    """
    try:
        data = await _get("search.json", params={"query": params.query, "per_page": params.limit})
        results = data.get("results", [])
        total = data.get("count", 0)

        if not results:
            return f"No results found for query: '{params.query}'"

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"total": total, "count": len(results), "results": results}, indent=2, default=str)

        lines = [f"## Zendesk Search: `{params.query}`", f"Showing {len(results)} of {total} results", ""]
        for r in results:
            rtype = r.get("result_type", "unknown")
            if rtype == "ticket":
                lines.append(
                    f"- **[Ticket #{r['id']}]** {r.get('subject', '(no subject)')} "
                    f"| Status: {r.get('status')} | Assignee: {r.get('assignee_id', '—')}"
                )
            elif rtype == "user":
                lines.append(f"- **[User #{r['id']}]** {r.get('name')} | {r.get('email')} | Role: {r.get('role')}")
            elif rtype == "organization":
                lines.append(f"- **[Org #{r['id']}]** {r.get('name')} | Domain: {r.get('domain_names', [])}")
            else:
                lines.append(f"- **[{rtype} #{r.get('id')}]** {r.get('name', r.get('subject', ''))}")
        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tools: Tickets
# ---------------------------------------------------------------------------


class ListTicketsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    status: Optional[str] = Field(
        default=None,
        description="Filter by status: 'new', 'open', 'pending', 'hold', 'solved', 'closed'"
    )
    group_id: Optional[int] = Field(default=None, description="Filter by group ID")
    assignee_id: Optional[int] = Field(default=None, description="Filter by assignee user ID")
    sort_by: Optional[str] = Field(
        default="updated_at",
        description="Sort field: 'created_at', 'updated_at', 'priority', 'status', 'ticket_type'"
    )
    sort_order: Optional[str] = Field(default="desc", description="'asc' or 'desc'")
    limit: Optional[int] = Field(default=25, ge=1, le=100, description="Max results (1–100, default 25)")
    page: Optional[int] = Field(default=1, ge=1, description="Page number (default 1)")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")


@mcp.tool(
    name="zendesk_list_tickets",
    annotations={
        "title": "List Zendesk Tickets",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_list_tickets(params: ListTicketsInput) -> str:
    """
    List Zendesk tickets with optional filters for status, group, and assignee.

    For complex queries (tags, date ranges, full-text), use zendesk_search instead.

    Args:
        params (ListTicketsInput):
            - status (str): 'new', 'open', 'pending', 'hold', 'solved', 'closed'
            - group_id (int): Filter by group ID
            - assignee_id (int): Filter by agent user ID
            - sort_by (str): Sort field (default 'updated_at')
            - sort_order (str): 'asc' or 'desc' (default 'desc')
            - limit (int): Max results (default 25)
            - page (int): Page number (default 1)
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: List of tickets with ID, subject, status, priority, group, and dates.
    """
    try:
        query_params: Dict[str, Any] = {
            "per_page": params.limit,
            "page": params.page,
            "sort_by": params.sort_by,
            "sort_order": params.sort_order,
        }
        if params.status:
            query_params["status"] = params.status

        endpoint = "tickets.json"
        if params.group_id:
            endpoint = f"groups/{params.group_id}/tickets.json"
        elif params.assignee_id:
            endpoint = f"users/{params.assignee_id}/tickets/assigned.json"

        data = await _get(endpoint, params=query_params)
        tickets = data.get("tickets", [])
        total = data.get("count", len(tickets))

        if not tickets:
            return "No tickets found matching the specified filters."

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"total": total, "count": len(tickets), "page": params.page, "tickets": tickets}, indent=2, default=str)

        lines = [f"## Tickets (Page {params.page})", f"Showing {len(tickets)} of {total}", ""]
        for t in tickets:
            lines.append(
                f"- **#{t['id']}** {t.get('subject', '(no subject)')} "
                f"| {t.get('status')} | {t.get('priority', '—')} priority "
                f"| Updated: {_fmt_datetime(t.get('updated_at'))}"
            )
        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


class GetTicketInput(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")
    ticket_id: int = Field(..., ge=1, description="Zendesk ticket ID")
    include_comments: bool = Field(default=True, description="Include all ticket comments/thread (default true)")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")


@mcp.tool(
    name="zendesk_get_ticket",
    annotations={
        "title": "Get Zendesk Ticket",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_get_ticket(params: GetTicketInput) -> str:
    """
    Retrieve a single Zendesk ticket by ID, including all custom fields and optionally its comment thread.

    Args:
        params (GetTicketInput):
            - ticket_id (int): Zendesk ticket ID
            - include_comments (bool): Include full comment thread (default true)
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Full ticket details including subject, status, priority, requester,
             assignee, group, tags, custom fields, and comments if requested.
    """
    try:
        ticket_data = await _get(f"tickets/{params.ticket_id}.json")
        t = ticket_data.get("ticket", {})

        comments_data: List[Dict] = []
        if params.include_comments:
            c_data = await _get(f"tickets/{params.ticket_id}/comments.json")
            comments_data = c_data.get("comments", [])

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"ticket": t, "comments": comments_data}, indent=2, default=str)

        tags = ", ".join(t.get("tags", [])) or "—"
        custom_fields = t.get("custom_fields", [])

        lines = [
            f"## Ticket #{t.get('id')}: {t.get('subject', '(no subject)')}",
            "",
            f"**Status**: {t.get('status')} | **Priority**: {t.get('priority', '—')} | **Type**: {t.get('type', '—')}",
            f"**Requester ID**: {t.get('requester_id', '—')} | **Assignee ID**: {t.get('assignee_id', '—')}",
            f"**Group ID**: {t.get('group_id', '—')} | **Organization ID**: {t.get('organization_id', '—')}",
            f"**Tags**: {tags}",
            f"**Created**: {_fmt_datetime(t.get('created_at'))} | **Updated**: {_fmt_datetime(t.get('updated_at'))}",
        ]

        if custom_fields:
            lines.append("\n**Custom Fields**:")
            for cf in custom_fields:
                if cf.get("value") is not None:
                    lines.append(f"  - Field {cf['id']}: {cf['value']}")

        if comments_data:
            lines.append(f"\n**Comments ({len(comments_data)})**:")
            for c in comments_data:
                author = c.get("author_id", "?")
                created = _fmt_datetime(c.get("created_at"))
                body = (c.get("plain_body") or c.get("body") or "").strip()
                public = "Public" if c.get("public") else "Internal"
                lines.append(f"\n---\n*{public} | Author: {author} | {created}*\n{body[:1000]}")

        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tools: Users
# ---------------------------------------------------------------------------


class ListUsersInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    role: Optional[str] = Field(
        default=None,
        description="Filter by role: 'end-user', 'agent', 'admin'"
    )
    organization_id: Optional[int] = Field(default=None, description="Filter by organization ID")
    limit: Optional[int] = Field(default=25, ge=1, le=100, description="Max results (default 25)")
    page: Optional[int] = Field(default=1, ge=1, description="Page number (default 1)")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")


@mcp.tool(
    name="zendesk_list_users",
    annotations={
        "title": "List Zendesk Users",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_list_users(params: ListUsersInput) -> str:
    """
    List Zendesk users filtered by role or organization.

    To search by name or email, use zendesk_search with 'type:user' query.

    Args:
        params (ListUsersInput):
            - role (str): 'end-user', 'agent', or 'admin'
            - organization_id (int): Filter to one org
            - limit (int): Max results (default 25)
            - page (int): Page number
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: List of users with ID, name, email, role, and org.
    """
    try:
        query_params: Dict[str, Any] = {"per_page": params.limit, "page": params.page}
        if params.role:
            query_params["role"] = params.role

        endpoint = "users.json"
        if params.organization_id:
            endpoint = f"organizations/{params.organization_id}/users.json"

        data = await _get(endpoint, params=query_params)
        users = data.get("users", [])
        total = data.get("count", len(users))

        if not users:
            return "No users found."

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"total": total, "count": len(users), "users": users}, indent=2, default=str)

        lines = [f"## Users (Page {params.page})", f"Showing {len(users)} of {total}", ""]
        for u in users:
            org = f"Org: {u.get('organization_id', '—')}"
            lines.append(
                f"- **#{u['id']}** {u.get('name')} | {u.get('email', '—')} "
                f"| {u.get('role')} | {org} | Active: {not u.get('suspended', False)}"
            )
        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


class GetUserInput(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")
    user_id: int = Field(..., ge=1, description="Zendesk user ID")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")


@mcp.tool(
    name="zendesk_get_user",
    annotations={
        "title": "Get Zendesk User",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_get_user(params: GetUserInput) -> str:
    """
    Retrieve a single Zendesk user by ID including phone, tags, and custom fields.

    Args:
        params (GetUserInput):
            - user_id (int): Zendesk user ID
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Full user profile including name, email, phone, role, org, tags, and custom fields.
    """
    try:
        data = await _get(f"users/{params.user_id}.json")
        u = data.get("user", {})

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"user": u}, indent=2, default=str)

        tags = ", ".join(u.get("tags", [])) or "—"
        custom_fields = u.get("user_fields", {})

        lines = [
            f"## User #{u.get('id')}: {u.get('name')}",
            "",
            f"**Email**: {u.get('email', '—')} | **Phone**: {u.get('phone', '—')}",
            f"**Role**: {u.get('role')} | **Organization ID**: {u.get('organization_id', '—')}",
            f"**Suspended**: {u.get('suspended', False)} | **Verified**: {u.get('verified', False)}",
            f"**Tags**: {tags}",
            f"**Created**: {_fmt_datetime(u.get('created_at'))} | **Last Login**: {_fmt_datetime(u.get('last_login_at'))}",
            f"**External ID**: {u.get('external_id', '—')}",
        ]

        if custom_fields:
            lines.append("\n**Custom Fields**:")
            for k, v in custom_fields.items():
                if v is not None:
                    lines.append(f"  - {k}: {v}")

        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tools: Organizations
# ---------------------------------------------------------------------------


class ListOrgsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    limit: Optional[int] = Field(default=25, ge=1, le=100, description="Max results (default 25)")
    page: Optional[int] = Field(default=1, ge=1, description="Page number (default 1)")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")


@mcp.tool(
    name="zendesk_list_organizations",
    annotations={
        "title": "List Zendesk Organizations",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_list_organizations(params: ListOrgsInput) -> str:
    """
    List all Zendesk organizations (customers/accounts).

    To search by name or domain, use zendesk_search with 'type:organization'.

    Args:
        params (ListOrgsInput):
            - limit (int): Max results (default 25)
            - page (int): Page number
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: List of organizations with ID, name, domain names, and tags.
    """
    try:
        data = await _get("organizations.json", params={"per_page": params.limit, "page": params.page})
        orgs = data.get("organizations", [])
        total = data.get("count", len(orgs))

        if not orgs:
            return "No organizations found."

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"total": total, "count": len(orgs), "organizations": orgs}, indent=2, default=str)

        lines = [f"## Organizations (Page {params.page})", f"Showing {len(orgs)} of {total}", ""]
        for o in orgs:
            domains = ", ".join(o.get("domain_names", [])) or "—"
            tags = ", ".join(o.get("tags", [])) or "—"
            lines.append(f"- **#{o['id']}** {o.get('name')} | Domains: {domains} | Tags: {tags}")
        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


class GetOrgInput(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")
    organization_id: int = Field(..., ge=1, description="Zendesk organization ID")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")


@mcp.tool(
    name="zendesk_get_organization",
    annotations={
        "title": "Get Zendesk Organization",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_get_organization(params: GetOrgInput) -> str:
    """
    Retrieve a single Zendesk organization by ID including domain names, tags, and custom fields.

    Args:
        params (GetOrgInput):
            - organization_id (int): Zendesk organization ID
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Full organization profile including name, domains, tags, notes, and custom fields.
    """
    try:
        data = await _get(f"organizations/{params.organization_id}.json")
        o = data.get("organization", {})

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"organization": o}, indent=2, default=str)

        domains = ", ".join(o.get("domain_names", [])) or "—"
        tags = ", ".join(o.get("tags", [])) or "—"
        custom_fields = o.get("organization_fields", {})

        lines = [
            f"## Organization #{o.get('id')}: {o.get('name')}",
            "",
            f"**Domains**: {domains}",
            f"**Tags**: {tags}",
            f"**Notes**: {o.get('notes', '—')}",
            f"**Created**: {_fmt_datetime(o.get('created_at'))} | **Updated**: {_fmt_datetime(o.get('updated_at'))}",
            f"**External ID**: {o.get('external_id', '—')}",
        ]

        if custom_fields:
            lines.append("\n**Custom Fields**:")
            for k, v in custom_fields.items():
                if v is not None:
                    lines.append(f"  - {k}: {v}")

        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tools: Groups
# ---------------------------------------------------------------------------


@mcp.tool(
    name="zendesk_list_groups",
    annotations={
        "title": "List Zendesk Groups",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_list_groups(response_format: str = "markdown") -> str:
    """
    List all Zendesk agent groups.

    Returns all groups regardless of size — no pagination needed as group counts are small.

    Args:
        response_format (str): 'markdown' or 'json' (default 'markdown')

    Returns:
        str: All groups with ID, name, description, and default status.
    """
    try:
        data = await _get("groups.json")
        groups = data.get("groups", [])

        if not groups:
            return "No groups found."

        fmt = ResponseFormat(response_format)
        if fmt == ResponseFormat.JSON:
            return json.dumps({"count": len(groups), "groups": groups}, indent=2, default=str)

        lines = [f"## Groups ({len(groups)} total)", ""]
        for g in groups:
            default = " *(default)*" if g.get("default") else ""
            desc = f" | {g['description']}" if g.get("description") else ""
            lines.append(f"- **#{g['id']}** {g.get('name')}{default}{desc}")
        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tools: Views
# ---------------------------------------------------------------------------


class GetViewTicketsInput(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")
    view_id: int = Field(..., ge=1, description="Zendesk view ID")
    limit: Optional[int] = Field(default=25, ge=1, le=100, description="Max results (default 25)")
    page: Optional[int] = Field(default=1, ge=1, description="Page number (default 1)")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")


@mcp.tool(
    name="zendesk_list_views",
    annotations={
        "title": "List Zendesk Views",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_list_views(response_format: str = "markdown") -> str:
    """
    List all active Zendesk views including their conditions and column configuration.

    Args:
        response_format (str): 'markdown' or 'json' (default 'markdown')

    Returns:
        str: All views with ID, title, active status, and restriction type.
    """
    try:
        data = await _get("views.json")
        views = data.get("views", [])

        if not views:
            return "No views found."

        fmt = ResponseFormat(response_format)
        if fmt == ResponseFormat.JSON:
            return json.dumps({"count": len(views), "views": views}, indent=2, default=str)

        lines = [f"## Views ({len(views)} total)", ""]
        for v in views:
            active = "Active" if v.get("active") else "Inactive"
            restriction = v.get("restriction", {})
            restricted_to = restriction.get("type", "All agents") if restriction else "All agents"
            lines.append(
                f"- **#{v['id']}** {v.get('title')} | {active} | Visible to: {restricted_to}"
            )
        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


@mcp.tool(
    name="zendesk_get_view_tickets",
    annotations={
        "title": "Get Tickets in a Zendesk View",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_get_view_tickets(params: GetViewTicketsInput) -> str:
    """
    Retrieve the tickets currently returned by a specific Zendesk view.

    Use zendesk_list_views first to find view IDs.

    Args:
        params (GetViewTicketsInput):
            - view_id (int): View ID to execute
            - limit (int): Max results (default 25)
            - page (int): Page number
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Tickets matching the view conditions with ID, subject, status, and assignee.
    """
    try:
        data = await _get(
            f"views/{params.view_id}/tickets.json",
            params={"per_page": params.limit, "page": params.page},
        )
        tickets = data.get("tickets", [])
        total = data.get("count", len(tickets))

        if not tickets:
            return f"No tickets in view #{params.view_id}."

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"view_id": params.view_id, "total": total, "tickets": tickets}, indent=2, default=str)

        lines = [f"## View #{params.view_id} Tickets (Page {params.page})", f"Showing {len(tickets)} of {total}", ""]
        for t in tickets:
            lines.append(
                f"- **#{t['id']}** {t.get('subject', '(no subject)')} "
                f"| {t.get('status')} | Updated: {_fmt_datetime(t.get('updated_at'))}"
            )
        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tools: Ticket Fields
# ---------------------------------------------------------------------------


@mcp.tool(
    name="zendesk_list_ticket_fields",
    annotations={
        "title": "List Zendesk Ticket Fields",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_list_ticket_fields(response_format: str = "markdown") -> str:
    """
    List all Zendesk ticket fields (system and custom), including field IDs and types.

    Useful for understanding what custom fields are available, their IDs (for mapping
    ticket data), and allowed values for dropdown fields.

    Args:
        response_format (str): 'markdown' or 'json' (default 'markdown')

    Returns:
        str: All ticket fields with ID, type, title, and allowed values for dropdowns.
    """
    try:
        data = await _get("ticket_fields.json")
        fields = data.get("ticket_fields", [])

        if not fields:
            return "No ticket fields found."

        fmt = ResponseFormat(response_format)
        if fmt == ResponseFormat.JSON:
            return json.dumps({"count": len(fields), "ticket_fields": fields}, indent=2, default=str)

        lines = [f"## Ticket Fields ({len(fields)} total)", ""]
        for f in fields:
            active = "Active" if f.get("active") else "Inactive"
            values = ""
            if f.get("custom_field_options"):
                opts = [o.get("value", "") for o in f["custom_field_options"]]
                values = f" | Values: {', '.join(opts[:5])}"
                if len(opts) > 5:
                    values += f" (+{len(opts) - 5} more)"
            lines.append(
                f"- **#{f['id']}** [{f.get('type')}] {f.get('title')} | {active}{values}"
            )
        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tools: Triggers
# ---------------------------------------------------------------------------


class ListTriggersInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    active: Optional[bool] = Field(default=None, description="Filter to active (True) or inactive (False) triggers")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")


@mcp.tool(
    name="zendesk_list_triggers",
    annotations={
        "title": "List Zendesk Triggers",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_list_triggers(params: ListTriggersInput) -> str:
    """
    List Zendesk triggers (business rules that fire on ticket create/update events).

    Args:
        params (ListTriggersInput):
            - active (bool): True for active only, False for inactive only, None for all
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: All triggers with ID, title, active status, and position.
    """
    try:
        query_params: Dict[str, Any] = {}
        if params.active is not None:
            query_params["active"] = str(params.active).lower()

        data = await _get("triggers.json", params=query_params)
        triggers = data.get("triggers", [])

        if not triggers:
            return "No triggers found."

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"count": len(triggers), "triggers": triggers}, indent=2, default=str)

        lines = [f"## Triggers ({len(triggers)} total)", ""]
        for t in triggers:
            active = "Active" if t.get("active") else "Inactive"
            lines.append(f"- **#{t['id']}** {t.get('title')} | {active} | Position: {t.get('position', '—')}")
        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


class GetTriggerInput(BaseModel):
    model_config = ConfigDict(validate_assignment=True, extra="forbid")
    trigger_id: int = Field(..., ge=1, description="Zendesk trigger ID")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")


@mcp.tool(
    name="zendesk_get_trigger",
    annotations={
        "title": "Get Zendesk Trigger",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_get_trigger(params: GetTriggerInput) -> str:
    """
    Retrieve a single Zendesk trigger including its full conditions and action definitions.

    Args:
        params (GetTriggerInput):
            - trigger_id (int): Trigger ID
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: Full trigger definition including all conditions (all/any) and actions.
    """
    try:
        data = await _get(f"triggers/{params.trigger_id}.json")
        t = data.get("trigger", {})

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"trigger": t}, indent=2, default=str)

        conditions = t.get("conditions", {})
        all_conds = conditions.get("all", [])
        any_conds = conditions.get("any", [])
        actions = t.get("actions", [])

        lines = [
            f"## Trigger #{t.get('id')}: {t.get('title')}",
            f"**Active**: {t.get('active')} | **Position**: {t.get('position', '—')}",
            "",
            "**Conditions (ALL must be true)**:" if all_conds else "",
        ]
        for c in all_conds:
            lines.append(f"  - {c.get('field')} {c.get('operator')} {c.get('value', '')}")

        if any_conds:
            lines.append("**Conditions (ANY must be true)**:")
            for c in any_conds:
                lines.append(f"  - {c.get('field')} {c.get('operator')} {c.get('value', '')}")

        if actions:
            lines.append("\n**Actions**:")
            for a in actions:
                lines.append(f"  - {a.get('field')}: {a.get('value', '')}")

        return "\n".join(filter(None, lines))
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Tools: Macros
# ---------------------------------------------------------------------------


class ListMacrosInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    active: Optional[bool] = Field(default=True, description="Filter to active (True) or inactive (False) macros. Default True.")
    limit: Optional[int] = Field(default=25, ge=1, le=100, description="Max results (default 25)")
    page: Optional[int] = Field(default=1, ge=1, description="Page number (default 1)")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN, description="'markdown' or 'json'")


@mcp.tool(
    name="zendesk_list_macros",
    annotations={
        "title": "List Zendesk Macros",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_list_macros(params: ListMacrosInput) -> str:
    """
    List Zendesk macros (one-click ticket actions used by agents).

    Args:
        params (ListMacrosInput):
            - active (bool): True for active only (default), False for inactive, None for all
            - limit (int): Max results (default 25)
            - page (int): Page number
            - response_format (str): 'markdown' or 'json'

    Returns:
        str: List of macros with ID, title, active status, and restriction type.
    """
    try:
        query_params: Dict[str, Any] = {"per_page": params.limit, "page": params.page}
        if params.active is not None:
            query_params["active"] = str(params.active).lower()

        data = await _get("macros.json", params=query_params)
        macros = data.get("macros", [])
        total = data.get("count", len(macros))

        if not macros:
            return "No macros found."

        if params.response_format == ResponseFormat.JSON:
            return json.dumps({"total": total, "count": len(macros), "macros": macros}, indent=2, default=str)

        lines = [f"## Macros (Page {params.page})", f"Showing {len(macros)} of {total}", ""]
        for m in macros:
            active = "Active" if m.get("active") else "Inactive"
            restriction = m.get("restriction")
            scope = restriction.get("type", "All agents") if restriction else "All agents"
            lines.append(f"- **#{m['id']}** {m.get('title')} | {active} | Scope: {scope}")
        return "\n".join(lines)
    except Exception as e:
        return _handle_error(e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
