"""Preflight MCP server: exposes the email QA checks as tools for AI agents."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import checks, sfmc

server = MCPServer(
    name="preflight",
    title="Preflight: email campaign QA",
    version="0.1.0",
    instructions=(
        "Preflight checks marketing emails before they are sent. "
        "For a full review, call preflight_email with the email HTML (or preflight_sfmc_email "
        "with a Content Builder asset ID). Explain blocking errors first, then warnings, "
        "and suggest concrete fixes. Use summarise_campaign_results for post-send performance. "
        "Compliance results are heuristics: recommend human sign-off."
    ),
)


@server.tool()
def preflight_email(html: str, region: str = "EU", expected_utm_medium: str = "email") -> dict:
    """Run the full pre-send QA on an email: content, UTM tracking and compliance.

    Args:
        html: The email's full HTML.
        region: "EU" (GDPR), "US" (CAN-SPAM) or "GLOBAL".
        expected_utm_medium: The utm_medium value every tracked link should use.
    """
    return checks.preflight(html, region=region, expected_utms={"utm_medium": expected_utm_medium})


@server.tool()
def check_email_content(html: str) -> dict:
    """Check links, images, alt text, unrendered merge tags, size, accessibility and spam phrases."""
    return checks.check_content(html)


@server.tool()
def validate_utm_links(html: str, required: list[str] | None = None,
                       expected: dict[str, str] | None = None,
                       tracked_domains: list[str] | None = None) -> dict:
    """Check that every tracked link carries the required UTM parameters and follows naming rules.

    Args:
        html: The email's full HTML.
        required: UTM keys each link must have (default: utm_source, utm_medium, utm_campaign).
        expected: Exact values to enforce, e.g. {"utm_medium": "email"}.
        tracked_domains: Only check links to these domains (default: all web links).
    """
    return checks.validate_utms(html, required=required, expected=expected, tracked_domains=tracked_domains)


@server.tool()
def check_email_compliance(html: str, region: str = "EU") -> dict:
    """Check for unsubscribe link, postal address, privacy link and consent wording.

    Args:
        html: The email's full HTML.
        region: "EU" (GDPR), "US" (CAN-SPAM) or "GLOBAL".
    """
    return checks.check_compliance(html, region=region)


@server.tool()
def summarise_campaign_results(csv_text: str, open_rate_floor: float = 15.0, ctr_floor: float = 1.0,
                               bounce_ceiling: float = 2.0, unsubscribe_ceiling: float = 0.5) -> dict:
    """Summarise email campaign performance from CSV and flag weak campaigns.

    Args:
        csv_text: CSV with columns campaign, sent, delivered, unique_opens, unique_clicks, bounces, unsubscribes.
        open_rate_floor: Flag campaigns with an open rate below this percentage.
        ctr_floor: Flag campaigns with a click-through rate below this percentage.
        bounce_ceiling: Flag campaigns with a bounce rate above this percentage.
        unsubscribe_ceiling: Flag campaigns with an unsubscribe rate above this percentage.
    """
    return checks.summarise_results(csv_text, open_rate_floor, ctr_floor, bounce_ceiling, unsubscribe_ceiling)


@server.tool()
def preflight_sfmc_email(asset_id: int, region: str = "EU", expected_utm_medium: str = "email") -> dict:
    """Fetch an email from Salesforce Marketing Cloud Content Builder by asset ID and run the full QA.

    Requires SFMC API credentials in the server environment (see README).

    Args:
        asset_id: The Content Builder asset ID of the email.
        region: "EU" (GDPR), "US" (CAN-SPAM) or "GLOBAL".
        expected_utm_medium: The utm_medium value every tracked link should use.
    """
    try:
        email = sfmc.fetch_email(asset_id)
    except sfmc.SFMCConfigError as e:
        return {"error": str(e)}
    report = checks.preflight(email["html"], region=region, expected_utms={"utm_medium": expected_utm_medium})
    report["email"] = {k: email[k] for k in ("asset_id", "name", "subject", "preheader")}
    report["subject_checks"] = checks.check_subject(email["subject"], email["preheader"])
    return report


@server.tool()
def check_subject_line(subject: str, preheader: str = "") -> dict:
    """Check a subject line and preheader for length, spam phrases and unrendered merge tags."""
    return checks.check_subject(subject, preheader)


@server.prompt()
def preflight_review(region: str = "EU") -> str:
    """A ready-made instruction for a full pre-send review."""
    return (
        f"Act as a marketing operations QA lead. Run preflight_email on the email I share (region {region}). "
        "Report: 1) a one-line verdict with the score, 2) blocking issues with exact fixes, "
        "3) warnings in priority order, 4) anything that needs human sign-off. Keep it concise."
    )


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
