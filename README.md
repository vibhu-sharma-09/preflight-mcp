# Preflight MCP

**Email campaign QA for AI agents.** Preflight is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that gives AI assistants such as Claude a set of precise pre-send checks for marketing emails. Instead of working through a manual QA checklist, you ask:

> "Preflight this email before we send it."

The agent decides which checks to run, calls Preflight's tools, and explains what is wrong and how to fix it.

## Why this exists

Pre-send QA on a busy email programme is slow and repetitive: broken links, missing UTM parameters, unrendered personalisation tags, missing alt text, a footer without an unsubscribe link. Mistakes cost revenue, data quality and compliance.

Major marketing platforms now ship MCP servers so AI agents can work inside them. Preflight adds the missing QA layer: deterministic, testable checks that any MCP-compatible agent can call, with an optional connector to Salesforce Marketing Cloud.

## What it checks

| Tool | What it does |
|---|---|
| `preflight_email` | Full pre-send review (content, UTMs and compliance) with a verdict and score |
| `check_email_content` | Empty, `javascript:` or staging links; http links and images; missing alt text; unrendered merge tags (SFMC, Mailchimp, Handlebars/Liquid); Gmail clipping size; spam-trigger phrases; `lang`, `<title>` and preheader |
| `validate_utm_links` | Required UTM parameters on every tracked link, enforced values (e.g. `utm_medium=email`), lowercase naming, consistent `utm_campaign`. Unsubscribe, legal and `mailto:` links are skipped |
| `check_email_compliance` | Unsubscribe or preference link, postal address, privacy link and consent wording, by region (EU/GDPR, US/CAN-SPAM or global) |
| `check_subject_line` | Subject and preheader length, capitals, exclamation marks, spam phrases, merge tags |
| `summarise_campaign_results` | Open rate, CTR, click-to-open, bounce and unsubscribe rates from a CSV export, with flags for weak campaigns |
| `preflight_sfmc_email` | Fetches an email from Salesforce Marketing Cloud Content Builder by asset ID and runs the full review |

It also includes a `preflight_review` prompt that sets the agent up as a QA lead.

## Example

Running `preflight_email` on [`examples/broken_email.html`](examples/broken_email.html) returns **FAIL** with 5 blocking issues and 9 warnings, including:

- Link "blog" points to a staging address and has no UTM parameters
- Link "download the slides" has no destination (`href="#"`)
- Link "Click here" uses `utm_medium=Email` instead of `email`, and a campaign name with a space
- Possible unrendered SFMC merge tag `%%FirstName%%`
- Logo image has no alt text and loads over http
- No unsubscribe or preference-centre link

The corrected version, [`examples/clean_email.html`](examples/clean_email.html), returns **PASS** with a score of 100.

## Quick start

Requires Python 3.10 or later.

```bash
git clone https://github.com/codestudio09/preflight-mcp.git
cd preflight-mcp
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                              # 15 tests
```

### Connect it to Claude Desktop

Open Claude Desktop, go to **Settings > Developer > Edit Config**, and add Preflight to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "preflight": {
      "command": "/full/path/to/preflight-mcp/.venv/bin/preflight-mcp"
    }
  }
}
```

On Windows the command is `C:\\full\\path\\to\\preflight-mcp\\.venv\\Scripts\\preflight-mcp.exe`. Restart Claude Desktop; the Preflight tools appear in the tools menu. Paste an email's HTML and ask Claude to preflight it.

### Optional: connect to Salesforce Marketing Cloud

`preflight_sfmc_email` reads emails straight from Content Builder. An SFMC admin creates an **Installed Package** with an **API Integration (server-to-server)** component and read access to Content Builder, then you add the credentials to the server's environment:

```json
"preflight": {
  "command": "/full/path/to/preflight-mcp/.venv/bin/preflight-mcp",
  "env": {
    "SFMC_SUBDOMAIN": "your-tenant-subdomain",
    "SFMC_CLIENT_ID": "...",
    "SFMC_CLIENT_SECRET": "...",
    "SFMC_ACCOUNT_ID": "optional business unit MID"
  }
}
```

The connector follows Salesforce's documented REST endpoints and is covered by mocked tests; try it in a sandbox business unit first. Use it only with your organisation's approval.

## How it is built

- `src/preflight/checks.py`: the checks as plain Python functions, independent of MCP and easy to test
- `src/preflight/sfmc.py`: Salesforce Marketing Cloud connector (OAuth client credentials, Content Builder asset API)
- `src/preflight/server.py`: exposes the checks as MCP tools using the official MCP Python SDK
- `tests/`: unit tests, including a mocked SFMC API

Compliance checks are heuristics that flag likely gaps; final sign-off stays with a person.

## Roadmap

- Rendering checks across email clients
- Link status checks (detect 404s)
- Brand rules from a config file (approved domains, tone, banned words)
- A multi-agent workflow: a planning agent builds a campaign through a platform's MCP server, and Preflight approves it before send

## Licence

MIT
