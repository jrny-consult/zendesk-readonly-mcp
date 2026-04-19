# Zendesk Read-Only MCP — Setup Guide

## What this does

Connects Claude Desktop, Cursor, custom apps, or another MCP-compatible client directly to a Zendesk instance for live read-only data queries during analysis sessions. No more CSV exports for quick lookups.

**Available tools (all read-only):**

| Tool                         | What it does                                     |
| ---------------------------- | ------------------------------------------------ |
| `zendesk_search`             | Unified search across tickets, users, orgs       |
| `zendesk_list_tickets`       | List tickets filtered by status, group, assignee |
| `zendesk_get_ticket`         | Full ticket detail including comment thread      |
| `zendesk_list_users`         | List users by role or org                        |
| `zendesk_get_user`           | Full user profile including custom fields        |
| `zendesk_list_organizations` | List all orgs                                    |
| `zendesk_get_organization`   | Full org profile including custom fields         |
| `zendesk_list_groups`        | All agent groups                                 |
| `zendesk_list_views`         | All views with config                            |
| `zendesk_get_view_tickets`   | Tickets currently in a view                      |
| `zendesk_list_ticket_fields` | All ticket fields with types and values          |
| `zendesk_list_triggers`      | All triggers with active/inactive filter         |
| `zendesk_get_trigger`        | Full trigger with conditions and actions         |
| `zendesk_list_macros`        | All macros with scope                            |
| `zendesk_get_endpoint`       | Read another Zendesk API v2 `.json` endpoint     |

---

## 1. Install dependencies

```bash
pip install "mcp[cli]" httpx pydantic
```

---

## 2. Get your Zendesk API token

1. Log into your Zendesk instance as an admin
2. Go to **Admin Center → Apps & Integrations → APIs → Zendesk API**
3. Under **Token access**, enable API token access
4. Click **Add API token**, give it a descriptive name, copy the token

---

## 3. Configure an MCP Client

This server uses local stdio transport. Most local MCP clients use the same shape: `command`, `args`, and `env`.

### Claude Desktop

Add this to your `claude_desktop_config.json` (on Mac: `~/Library/Application Support/Claude/claude_desktop_config.json`):

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

Replace `/absolute/path/to/zendesk_mcp.py` with the actual path to this file, and `your_token_here` with the token from step 2.

---

### Cursor or another JSON-config client

If your client supports stdio MCP servers through a JSON config file, use the same server block:

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

Some clients store this globally. Others store it per project. The server block is the part that matters.

### Custom apps

Your own app can launch the server as a subprocess with stdio MCP transport:

1. Run `python3 /absolute/path/to/zendesk_mcp.py`.
2. Pass Zendesk credentials as environment variables.
3. Connect with an MCP client SDK that supports stdio.
4. List tools and call the Zendesk tool needed by the app workflow.

For hosted or remote-only clients, add a remote MCP wrapper with OAuth, tenant isolation, encrypted token storage, audit logs, and rate limiting before exposing the server outside a trusted local environment.

---

## 4. Restart the Client

After saving the config, restart the MCP client. The Zendesk tools should appear automatically.

---

## Usage examples

- _"Search for all open tickets tagged rma"_
  → `zendesk_search` with `type:ticket status:open tag:rma`

- _"How many tickets is the PCS group handling?"_
  → `zendesk_list_groups` → `zendesk_get_view_tickets` for that group

- _"Show me the full trigger for ticket assignment to T1"_
  → `zendesk_list_triggers` → `zendesk_get_trigger`

- _"Find all unassigned users with the aiworldwide.com domain"_
  → `zendesk_search` with `type:user email:@aiworldwide.com`

- _"Read the brands endpoint"_
  → `zendesk_get_endpoint` with `endpoint: "brands.json"`

- _"Read help center articles with a limit"_
  → `zendesk_get_endpoint` with `endpoint: "help_center/articles.json"` and `params: {"per_page": 25}`

---

## Notes

- This MCP is **read-only** — it cannot create, update, or delete any Zendesk data.
- Additional endpoints can be reached with `zendesk_get_endpoint` as long as they are relative Zendesk API v2 `.json` GET endpoints.
- Rate limit on Zendesk Enterprise: 700 requests/minute. Normal analysis sessions won't hit this.
- All tools support `response_format: "json"` for programmatic use, or `"markdown"` (default) for readable output.
