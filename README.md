# Zendesk Read-Only MCP Server

A cloneable Model Context Protocol server that lets Claude Desktop and other MCP-compatible clients query Zendesk through read-only tools.

## What It Does

This server connects to the Zendesk REST API with an API token stored in local environment variables. It exposes safe read-only tools for common support analysis work:

- Search tickets, users, and organizations with Zendesk search syntax
- List tickets by status, group, assignee, sort order, and page
- Fetch full ticket detail including comment thread context
- List and inspect users, organizations, groups, and views
- Inspect ticket fields, triggers, and macros
- Call additional read-only Zendesk API v2 `.json` endpoints without writing a new tool
- Return results as readable markdown or structured JSON

## Safety Posture

- Read-only by design
- No create, update, delete, merge, solve, or requester-change tools
- No secrets committed to the repo
- Zendesk credentials are supplied by the local MCP client config
- Tool inputs are validated with Pydantic

## Requirements

- Python 3.10+
- Zendesk API token
- Claude Desktop or another MCP-compatible client

Install dependencies:

```bash
pip install -r requirements.txt
```

## Zendesk Credentials

Create a Zendesk API token in Zendesk Admin Center:

1. Open Admin Center
2. Go to Apps and integrations
3. Open Zendesk API
4. Enable token access
5. Add an API token

Use `.env.example` as the reference for required values:

```bash
ZENDESK_SUBDOMAIN=your-subdomain
ZENDESK_EMAIL=admin@example.com
ZENDESK_API_TOKEN=your_zendesk_api_token
```

Do not commit `.env` or real tokens.

## Claude Desktop Setup

Add the server to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "python3",
      "args": [
        "/absolute/path/to/zendesk_mcp.py"
      ],
      "env": {
        "ZENDESK_SUBDOMAIN": "your-subdomain",
        "ZENDESK_EMAIL": "admin@example.com",
        "ZENDESK_API_TOKEN": "your_token_here"
      }
    }
  }
}
```

Restart Claude Desktop after saving the config.

## Available Tools

| Tool | Purpose |
| --- | --- |
| `zendesk_search` | Unified search across tickets, users, and organizations |
| `zendesk_list_tickets` | List tickets filtered by status, group, and assignee |
| `zendesk_get_ticket` | Fetch full ticket detail and comments |
| `zendesk_list_users` | List users by role or organization |
| `zendesk_get_user` | Fetch a full user profile |
| `zendesk_list_organizations` | List organizations |
| `zendesk_get_organization` | Fetch a full organization profile |
| `zendesk_list_groups` | List agent groups |
| `zendesk_list_views` | List views and view configuration |
| `zendesk_get_view_tickets` | List tickets currently in a view |
| `zendesk_list_ticket_fields` | List ticket fields and values |
| `zendesk_list_triggers` | List triggers by active/inactive status |
| `zendesk_get_trigger` | Fetch full trigger conditions and actions |
| `zendesk_list_macros` | List macros and scope |
| `zendesk_get_endpoint` | Call an additional read-only Zendesk API v2 `.json` endpoint |

## Adding More Endpoints

The repo supports two extension paths.

### Option 1: Use the Generic Read-Only Endpoint Tool

Use `zendesk_get_endpoint` for a Zendesk API v2 endpoint that is not modeled yet:

```json
{
  "endpoint": "brands.json",
  "params": {
    "page": 1,
    "per_page": 25
  },
  "response_format": "json"
}
```

Rules:

- Use a relative endpoint only, such as `brands.json` or `help_center/articles.json`
- Do not include a full URL
- Do not include query strings in the endpoint; put query parameters in `params`
- Only `.json` endpoints are accepted
- The server still makes a GET request only

### Option 2: Add a Typed Tool

For endpoints that buyers use often, add a dedicated Pydantic input model and MCP tool:

```python
class ListBrandsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True, extra="forbid")
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


@mcp.tool(
    name="zendesk_list_brands",
    annotations={
        "title": "List Zendesk Brands",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def zendesk_list_brands(params: ListBrandsInput) -> str:
    try:
        data = await _get("brands.json")
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(data, indent=2, default=str)
        return "\n".join([f"- **#{brand['id']}** {brand.get('name')}" for brand in data.get("brands", [])])
    except Exception as e:
        return _handle_error(e)
```

Keep new tools read-only unless a client has explicitly approved write actions.

## Example Prompts

```text
Search for all open tickets tagged rma.
```

```text
Show the full trigger for ticket assignment to Tier 1.
```

```text
List the current tickets in the escalations view.
```

```text
Find users with email addresses from example.com.
```

## Packaging For Clients

This repo can be delivered as a GitHub template or private repo. Before sharing it:

- Confirm no real tokens appear in docs, shell history, commits, or screenshots
- Keep default tools read-only
- Add client-specific tools in a branch or fork
- Include setup support if the buyer is not comfortable editing MCP client config
