"""Core QA checks for email campaigns.

Every check is a plain function that takes HTML (or CSV text) and returns a
dictionary that is easy for both people and AI agents to read. The MCP server
in ``server.py`` exposes these functions as tools; keeping them separate makes
them simple to test.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

# Gmail clips messages whose HTML is larger than about 102 KB.
GMAIL_CLIP_KB = 102

# Unrendered merge tags from common email platforms.
MERGE_TAG_PATTERNS = {
    "Salesforce Marketing Cloud": r"%%(?![\[=])[A-Za-z_][A-Za-z0-9_]*%%",
    "Mailchimp": r"\*\|[A-Z0-9_:]+\|\*",
    "Handlebars / Liquid": r"\{\{\s*[^}]+\s*\}\}",
    "Bracket placeholder": r"\[(?:First ?Name|Last ?Name|Company|Email)\]",
}

SPAM_PHRASES = [
    "act now", "100% free", "risk-free", "guaranteed", "winner", "click here",
    "limited time only", "buy now", "cash bonus", "no obligation", "urgent",
    "100% satisfied", "earn money", "once in a lifetime", "double your",
]

UNSUBSCRIBE_HINTS = ("unsubscribe", "opt-out", "opt out", "manage preferences",
                     "email preferences", "subscription preferences")

DEFAULT_REQUIRED_UTMS = ("utm_source", "utm_medium", "utm_campaign")


@dataclass
class Issue:
    check: str
    severity: str  # "error" blocks sending, "warning" should be reviewed, "info" is advisory
    message: str
    detail: str = ""


@dataclass
class Result:
    tool: str
    issues: list[Issue] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, check: str, severity: str, message: str, detail: str = "") -> None:
        self.issues.append(Issue(check, severity, message, detail))

    def to_dict(self) -> dict:
        counts = {s: sum(1 for i in self.issues if i.severity == s)
                  for s in ("error", "warning", "info")}
        if counts["error"]:
            verdict = "FAIL"
        elif counts["warning"]:
            verdict = "REVIEW"
        else:
            verdict = "PASS"
        return {
            "tool": self.tool,
            "verdict": verdict,
            "counts": counts,
            "stats": self.stats,
            "issues": [asdict(i) for i in self.issues],
        }


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _is_unsubscribe(href: str, text: str) -> bool:
    blob = f"{href} {text}".lower()
    return "unsub" in blob or any(h in blob for h in UNSUBSCRIBE_HINTS)


UTILITY_HINTS = ("privacy", "terms", "legal", "cookie", "view in browser", "view online", "webversion")


def _is_tracking_exempt(href: str, text: str = "") -> bool:
    """Links that normally carry no campaign tracking: mailto/tel, legal and view-online links."""
    if href.lower().startswith(("mailto:", "tel:", "sms:")):
        return True
    blob = f"{href} {text}".lower()
    return any(h in blob for h in UTILITY_HINTS)


# ---------------------------------------------------------------------------
# 1. Content check
# ---------------------------------------------------------------------------
def check_content(html: str) -> dict:
    """Check links, images, merge tags, size, accessibility and spam phrases."""
    res = Result("check_content")
    soup = _soup(html)

    # Size
    size_kb = round(len(html.encode("utf-8")) / 1024, 1)
    res.stats["html_size_kb"] = size_kb
    if size_kb > GMAIL_CLIP_KB:
        res.add("size", "error", f"HTML is {size_kb} KB; Gmail clips emails above {GMAIL_CLIP_KB} KB.",
                "Clipping can hide the footer and unsubscribe link.")
    elif size_kb > GMAIL_CLIP_KB * 0.85:
        res.add("size", "warning", f"HTML is {size_kb} KB, close to Gmail's {GMAIL_CLIP_KB} KB clipping limit.")

    # Links
    links = soup.find_all("a")
    res.stats["links"] = len(links)
    for a in links:
        href = (a.get("href") or "").strip()
        label = a.get_text(" ", strip=True)[:60] or "(no link text)"
        if not href or href == "#":
            res.add("links", "error", f"Link '{label}' has no destination.", f"href='{href}'")
        elif href.lower().startswith("javascript:"):
            res.add("links", "error", f"Link '{label}' uses javascript:, which email clients block.")
        elif re.search(r"(localhost|127\.0\.0\.1|staging\.|\.test/|example\.com)", href, re.I):
            res.add("links", "error", f"Link '{label}' points to a test or staging address.", href)
        elif href.lower().startswith("http://"):
            res.add("links", "warning", f"Link '{label}' is not secure (http).", href)
        if not a.get_text(strip=True) and not a.find("img"):
            res.add("accessibility", "warning", "A link has no visible text or image.", href)
        elif label.lower() in ("click here", "here", "read more", "learn more"):
            res.add("accessibility", "info", f"Link text '{label}' is vague for screen-reader users.", href)

    # Images
    images = soup.find_all("img")
    res.stats["images"] = len(images)
    for img in images:
        src = img.get("src", "")
        if img.get("alt") is None:
            res.add("images", "warning", "Image is missing alt text.", src)
        if src.lower().startswith("http://"):
            res.add("images", "warning", "Image is loaded over http.", src)

    # Text-to-image balance
    text = soup.get_text(" ", strip=True)
    res.stats["text_words"] = len(text.split())
    if images and len(text.split()) < 50:
        res.add("deliverability", "warning",
                "Very little live text relative to images; image-heavy emails are more likely to be filtered.")

    # Unrendered merge tags
    for platform, pattern in MERGE_TAG_PATTERNS.items():
        for m in sorted(set(re.findall(pattern, html))):
            res.add("personalisation", "warning",
                    f"Possible unrendered {platform} merge tag: {m}",
                    "Confirm it has a fallback value, or it may show raw code to recipients.")

    # Accessibility basics
    html_tag = soup.find("html")
    if html_tag is None or not html_tag.get("lang"):
        res.add("accessibility", "info", "The <html> tag has no lang attribute.",
                "Screen readers use it to choose pronunciation.")
    if not soup.find("title"):
        res.add("accessibility", "info", "No <title> element.")

    # Preheader
    if not soup.find(class_=re.compile("preheader", re.I)):
        res.add("content", "info", "No preheader found (element with class 'preheader').")

    # Spam phrases
    lower = text.lower()
    found = [p for p in SPAM_PHRASES if p in lower]
    if found:
        res.add("deliverability", "warning", "Spam-trigger phrases found: " + ", ".join(found))

    return res.to_dict()


# ---------------------------------------------------------------------------
# 2. UTM validation
# ---------------------------------------------------------------------------
def validate_utms(html: str, required: list[str] | None = None,
                  expected: dict[str, str] | None = None,
                  tracked_domains: list[str] | None = None) -> dict:
    """Check that tracked links carry the required UTM parameters.

    required: UTM keys every tracked link must have.
    expected: exact values to enforce, e.g. {"utm_medium": "email"}.
    tracked_domains: only check links to these domains (default: all web links).
    """
    res = Result("validate_utms")
    required = list(required or DEFAULT_REQUIRED_UTMS)
    expected = {k.lower(): v for k, v in (expected or {}).items()}
    domains = [d.lower() for d in (tracked_domains or [])]

    checked = 0
    campaigns = set()
    for a in _soup(html).find_all("a"):
        href = (a.get("href") or "").strip()
        label = a.get_text(" ", strip=True)[:60] or href[:60]
        if not href.lower().startswith(("http://", "https://")):
            continue
        if _is_tracking_exempt(href, label) or _is_unsubscribe(href, label):
            continue
        host = urlparse(href).netloc.lower()
        if domains and not any(host == d or host.endswith("." + d) for d in domains):
            continue
        checked += 1
        params = {k.lower(): v[0] for k, v in parse_qs(urlparse(href).query).items()}

        missing = [k for k in required if k not in params]
        if missing:
            res.add("utm", "error", f"Link '{label}' is missing {', '.join(missing)}.", href)
        for key, want in expected.items():
            if key in params and params[key] != want:
                res.add("utm", "error", f"Link '{label}' has {key}={params[key]}; expected {want}.", href)
        for key, val in params.items():
            if key.startswith("utm_") and (val != val.lower() or " " in val):
                res.add("utm", "warning",
                        f"Link '{label}' has {key}='{val}'. Use lowercase with no spaces so reports group correctly.",
                        href)
        if "utm_campaign" in params:
            campaigns.add(params["utm_campaign"])

    res.stats["links_checked"] = checked
    res.stats["utm_campaign_values"] = sorted(campaigns)
    if len(campaigns) > 1:
        res.add("utm", "warning", "Links use different utm_campaign values: " + ", ".join(sorted(campaigns)),
                "Results will be split across campaigns in analytics.")
    if checked == 0:
        res.add("utm", "info", "No trackable web links found.")
    return res.to_dict()


# ---------------------------------------------------------------------------
# 3. Compliance
# ---------------------------------------------------------------------------
_ADDRESS_HINT = re.compile(
    r"(\b\d{1,5}\s+\w+(\s\w+)*\s(street|st\.?|road|rd\.?|avenue|ave\.?|lane|way|boulevard|blvd)\b)"
    r"|(\b\d{5}(-\d{4})?\b)|(\b[A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2}\b)|(\b\d{6}\b)",
    re.I,
)


def check_compliance(html: str, region: str = "EU") -> dict:
    """Heuristic checks for unsubscribe, sender address and privacy wording.

    region: "EU" (GDPR and ePrivacy), "US" (CAN-SPAM) or "GLOBAL" (both).
    These checks flag likely gaps; final compliance sign-off stays with a person.
    """
    res = Result("check_compliance")
    region = region.upper()
    soup = _soup(html)
    text = soup.get_text(" ", strip=True)
    lower = text.lower()
    links = soup.find_all("a")

    unsub = [a for a in links if _is_unsubscribe(a.get("href") or "", a.get_text(" ", strip=True))]
    res.stats["unsubscribe_links"] = len(unsub)
    if not unsub:
        res.add("unsubscribe", "error", "No unsubscribe or preference-centre link found.",
                "Required under GDPR/ePrivacy and CAN-SPAM.")
    else:
        for a in unsub:
            href = (a.get("href") or "").strip()
            if not href or href == "#":
                res.add("unsubscribe", "error", "Unsubscribe link has no destination.")

    if not _ADDRESS_HINT.search(text):
        sev = "error" if region in ("US", "GLOBAL") else "warning"
        res.add("sender", sev, "No physical postal address detected in the email.",
                "CAN-SPAM requires a valid postal address; EU practice is to identify the sender clearly.")

    if region in ("EU", "GLOBAL"):
        privacy = any("privacy" in ((a.get("href") or "") + a.get_text()).lower() for a in links)
        if not privacy:
            res.add("privacy", "warning", "No privacy policy link found.",
                    "GDPR transparency: recipients should be able to find how their data is used.")
        if not re.search(r"(you (are )?receiv(e|ing) this|you (have )?(signed|subscribed|opted)|consent)", lower):
            res.add("consent", "info", "No wording that explains why the recipient is getting this email.",
                    "A short line on how they subscribed supports GDPR transparency.")

    res.stats["region"] = region
    res.add("note", "info", "Automated checks are heuristics; final compliance sign-off should be done by a person.")
    return res.to_dict()


# ---------------------------------------------------------------------------
# Subject line
# ---------------------------------------------------------------------------
def check_subject(subject: str, preheader: str = "") -> dict:
    """Check subject line and preheader length, spam phrases and merge tags."""
    res = Result("check_subject")
    s = (subject or "").strip()
    res.stats["subject_length"] = len(s)
    res.stats["preheader_length"] = len((preheader or "").strip())
    if not s:
        res.add("subject", "error", "Subject line is empty.")
    else:
        if len(s) > 60:
            res.add("subject", "warning", f"Subject is {len(s)} characters; many mobile clients cut off after about 40 to 60.")
        letters = [c for c in s if c.isalpha()]
        if len(letters) > 5 and sum(c.isupper() for c in letters) / len(letters) > 0.7:
            res.add("subject", "warning", "Subject is all capitals, a common spam signal.")
        if s.count("!") > 1:
            res.add("subject", "warning", "Subject has several exclamation marks.")
        found = [p for p in SPAM_PHRASES if p in s.lower()]
        if found:
            res.add("subject", "warning", "Spam-trigger phrases in subject: " + ", ".join(found))
    for label, text in (("subject", s), ("preheader", preheader or "")):
        for platform, pattern in MERGE_TAG_PATTERNS.items():
            for m in set(re.findall(pattern, text)):
                res.add(label, "warning", f"Possible unrendered {platform} merge tag in {label}: {m}")
    if not (preheader or "").strip():
        res.add("preheader", "info", "No preheader set; inboxes will show the first line of body text instead.")
    elif len(preheader.strip()) > 110:
        res.add("preheader", "info", "Preheader is over 110 characters; most inboxes show 40 to 100.")
    return res.to_dict()


# ---------------------------------------------------------------------------
# 4. Full preflight
# ---------------------------------------------------------------------------
def preflight(html: str, region: str = "EU", expected_utms: dict[str, str] | None = None) -> dict:
    """Run content, UTM and compliance checks and return one combined report."""
    parts = [check_content(html), validate_utms(html, expected=expected_utms), check_compliance(html, region)]
    errors = sum(p["counts"]["error"] for p in parts)
    warnings = sum(p["counts"]["warning"] for p in parts)
    score = max(0, 100 - errors * 15 - warnings * 4)
    verdict = "FAIL" if errors else ("REVIEW" if warnings else "PASS")
    return {
        "verdict": verdict,
        "score": score,
        "summary": f"{errors} blocking issue(s), {warnings} warning(s).",
        "checks": parts,
    }


# ---------------------------------------------------------------------------
# 5. Campaign results summary
# ---------------------------------------------------------------------------
REQUIRED_COLUMNS = ("campaign", "sent", "delivered", "unique_opens", "unique_clicks", "bounces", "unsubscribes")


def _pct(n: float, d: float) -> float:
    return round(100 * n / d, 2) if d else 0.0


def summarise_results(csv_text: str, open_rate_floor: float = 15.0,
                      ctr_floor: float = 1.0, bounce_ceiling: float = 2.0,
                      unsub_ceiling: float = 0.5) -> dict:
    """Summarise campaign performance from CSV text and flag weak campaigns.

    Expected columns: campaign, sent, delivered, unique_opens, unique_clicks, bounces, unsubscribes.
    Thresholds are percentages and can be changed per team.
    """
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    cols = [c.strip().lower() for c in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in cols]
    if missing:
        return {"error": f"CSV is missing columns: {', '.join(missing)}", "expected_columns": list(REQUIRED_COLUMNS)}

    rows, flags = [], []
    totals = dict.fromkeys(REQUIRED_COLUMNS[1:], 0)
    for raw in reader:
        r = {k.strip().lower(): (v or "").strip() for k, v in raw.items()}
        try:
            nums = {k: int(float(r[k])) for k in REQUIRED_COLUMNS[1:]}
        except ValueError:
            flags.append({"campaign": r.get("campaign", "?"), "issue": "Row has non-numeric values; skipped."})
            continue
        for k in totals:
            totals[k] += nums[k]
        m = {
            "campaign": r["campaign"],
            "delivery_rate": _pct(nums["delivered"], nums["sent"]),
            "open_rate": _pct(nums["unique_opens"], nums["delivered"]),
            "ctr": _pct(nums["unique_clicks"], nums["delivered"]),
            "click_to_open": _pct(nums["unique_clicks"], nums["unique_opens"]),
            "bounce_rate": _pct(nums["bounces"], nums["sent"]),
            "unsubscribe_rate": _pct(nums["unsubscribes"], nums["delivered"]),
        }
        rows.append(m)
        if m["open_rate"] < open_rate_floor:
            flags.append({"campaign": m["campaign"], "issue": f"Open rate {m['open_rate']}% is below {open_rate_floor}%."})
        if m["ctr"] < ctr_floor:
            flags.append({"campaign": m["campaign"], "issue": f"CTR {m['ctr']}% is below {ctr_floor}%."})
        if m["bounce_rate"] > bounce_ceiling:
            flags.append({"campaign": m["campaign"], "issue": f"Bounce rate {m['bounce_rate']}% is above {bounce_ceiling}%; check list hygiene."})
        if m["unsubscribe_rate"] > unsub_ceiling:
            flags.append({"campaign": m["campaign"], "issue": f"Unsubscribe rate {m['unsubscribe_rate']}% is above {unsub_ceiling}%; review relevance and frequency."})

    overall = {
        "campaigns": len(rows),
        "sent": totals["sent"],
        "open_rate": _pct(totals["unique_opens"], totals["delivered"]),
        "ctr": _pct(totals["unique_clicks"], totals["delivered"]),
        "click_to_open": _pct(totals["unique_clicks"], totals["unique_opens"]),
        "bounce_rate": _pct(totals["bounces"], totals["sent"]),
        "unsubscribe_rate": _pct(totals["unsubscribes"], totals["delivered"]),
    }
    ranked = sorted(rows, key=lambda x: x["ctr"], reverse=True)
    return {
        "overall": overall,
        "best_by_ctr": ranked[:3],
        "weakest_by_ctr": ranked[-3:][::-1] if len(ranked) > 3 else [],
        "flags": flags,
        "campaigns": rows,
        "thresholds": {"open_rate_floor": open_rate_floor, "ctr_floor": ctr_floor,
                       "bounce_ceiling": bounce_ceiling, "unsubscribe_ceiling": unsub_ceiling},
    }
