"""Optional connector that fetches email HTML from Salesforce Marketing Cloud.

It uses the SFMC REST API with an "API Integration" installed package
(server-to-server, client credentials). Set these environment variables:

    SFMC_SUBDOMAIN      the tenant subdomain, e.g. mc563885gzs27c5t9-63k636ttgm
    SFMC_CLIENT_ID      from the installed package
    SFMC_CLIENT_SECRET  from the installed package
    SFMC_ACCOUNT_ID     optional MID of the business unit

The package needs read access to Content Builder ("Documents and Images" and
"Saved Content" read permissions).

Note: this module follows Salesforce's documented endpoints but has not been
tested against a live account by the author. Use a sandbox business unit first.
"""

from __future__ import annotations

import os

import httpx


class SFMCConfigError(RuntimeError):
    pass


def _config() -> dict:
    sub = os.getenv("SFMC_SUBDOMAIN")
    cid = os.getenv("SFMC_CLIENT_ID")
    secret = os.getenv("SFMC_CLIENT_SECRET")
    if not (sub and cid and secret):
        raise SFMCConfigError(
            "SFMC is not configured. Set SFMC_SUBDOMAIN, SFMC_CLIENT_ID and SFMC_CLIENT_SECRET "
            "in the MCP server's environment, or paste the email HTML instead."
        )
    return {"sub": sub, "cid": cid, "secret": secret, "mid": os.getenv("SFMC_ACCOUNT_ID")}


def _token(cfg: dict, client: httpx.Client) -> str:
    body = {"grant_type": "client_credentials", "client_id": cfg["cid"], "client_secret": cfg["secret"]}
    if cfg["mid"]:
        body["account_id"] = cfg["mid"]
    r = client.post(f"https://{cfg['sub']}.auth.marketingcloudapis.com/v2/token", json=body)
    r.raise_for_status()
    return r.json()["access_token"]


def extract_html(asset: dict) -> str:
    """Pull the HTML out of a Content Builder asset response."""
    views = asset.get("views") or {}
    html = (views.get("html") or {}).get("content")
    if not html:
        html = asset.get("content", "")
    if not html:
        raise ValueError("The asset has no HTML content. Is it an email (not a block or image)?")
    return html


def fetch_email(asset_id: int) -> dict:
    """Return the name, subject and HTML of a Content Builder email by asset ID."""
    cfg = _config()
    with httpx.Client(timeout=30) as client:
        token = _token(cfg, client)
        r = client.get(
            f"https://{cfg['sub']}.rest.marketingcloudapis.com/asset/v1/content/assets/{asset_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        asset = r.json()
    views = asset.get("views") or {}
    subject = ((views.get("subjectline") or {}).get("content")) or ""
    preheader = ((views.get("preheader") or {}).get("content")) or ""
    return {
        "asset_id": asset_id,
        "name": asset.get("name", ""),
        "subject": subject,
        "preheader": preheader,
        "html": extract_html(asset),
    }
