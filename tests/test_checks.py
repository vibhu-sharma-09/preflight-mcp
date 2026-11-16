from pathlib import Path

from preflight import checks
from preflight.sfmc import extract_html

EX = Path(__file__).resolve().parent.parent / "examples"
BROKEN = (EX / "broken_email.html").read_text()
CLEAN = (EX / "clean_email.html").read_text()
CSV = (EX / "campaign_results.csv").read_text()


def messages(report):
    return " | ".join(i["message"] for i in report["issues"])


def test_clean_email_passes_full_preflight():
    r = checks.preflight(CLEAN, region="EU", expected_utms={"utm_medium": "email"})
    assert r["verdict"] == "PASS", r
    assert r["score"] == 100


def test_broken_email_fails_full_preflight():
    r = checks.preflight(BROKEN, region="EU", expected_utms={"utm_medium": "email"})
    assert r["verdict"] == "FAIL"
    assert r["score"] < 50


def test_content_finds_link_image_and_merge_tag_problems():
    r = checks.check_content(BROKEN)
    m = messages(r)
    assert "has no destination" in m                 # href="#"
    assert "test or staging address" in m            # staging link
    assert "missing alt text" in m
    assert "%%FirstName%%" in m
    assert "Spam-trigger phrases" in m
    assert r["verdict"] == "FAIL"


def test_merge_tag_detection_ignores_ampscript_blocks():
    html = '<html lang="en"><body>%%[ SET @x = 1 ]%% %%=v(@x)=%% <p>Hi %%FirstName%%</p></body></html>'
    found = [i["message"] for i in checks.check_content(html)["issues"] if i["check"] == "personalisation"]
    assert len(found) == 1 and "%%FirstName%%" in found[0]


def test_gmail_clipping_threshold():
    big = "<html lang='en'><body>" + ("<p>text</p>" * 12000) + "</body></html>"
    r = checks.check_content(big)
    assert any(i["check"] == "size" and i["severity"] == "error" for i in r["issues"])


def test_utm_validation_flags_case_spaces_and_wrong_medium():
    r = checks.validate_utms(BROKEN, expected={"utm_medium": "email"})
    m = messages(r)
    assert "expected email" in m                     # utm_medium=Email
    assert "lowercase" in m                          # "Q1 Webinar"
    assert "missing utm_source" in m                 # blog link has no UTMs
    assert "different utm_campaign values" in m


def test_utm_validation_skips_unsubscribe_and_mailto():
    html = ('<a href="https://x.com/unsubscribe?id=1">Unsubscribe</a>'
            '<a href="mailto:hi@x.com">Email us</a>')
    r = checks.validate_utms(html)
    assert r["stats"]["links_checked"] == 0


def test_tracked_domains_filter():
    html = '<a href="https://partner.org/page">Partner</a><a href="https://mysite.com/a">Mine</a>'
    r = checks.validate_utms(html, tracked_domains=["mysite.com"])
    assert r["stats"]["links_checked"] == 1


def test_compliance_eu_and_us():
    eu = checks.check_compliance(BROKEN, region="EU")
    assert eu["verdict"] == "FAIL"
    assert "No unsubscribe" in messages(eu)
    us = checks.check_compliance(BROKEN, region="US")
    assert any(i["check"] == "sender" and i["severity"] == "error" for i in us["issues"])
    clean = checks.check_compliance(CLEAN, region="GLOBAL")
    assert clean["counts"]["error"] == 0 and clean["counts"]["warning"] == 0


def test_subject_checks():
    r = checks.check_subject("ACT NOW!!! 100% FREE SEATS FOR %%FirstName%% AT OUR HUGE ANNUAL SUMMIT EVENT")
    m = messages(r)
    assert "all capitals" in m and "exclamation" in m and "%%FirstName%%" in m and "characters" in m
    assert checks.check_subject("")["verdict"] == "FAIL"
    assert checks.check_subject("Your Q1 webinar seat", "Live on 12 February")["verdict"] == "PASS"


def test_results_summary_maths_and_flags():
    r = checks.summarise_results(CSV)
    o = r["overall"]
    assert o["campaigns"] == 6
    assert o["sent"] == 48080
    digest = next(c for c in r["campaigns"] if c["campaign"] == "Weekly Digest - Wk 1")
    assert digest["open_rate"] == round(100 * 1660 / 12390, 2)
    assert digest["ctr"] == round(100 * 171 / 12390, 2)
    flagged = {f["campaign"] for f in r["flags"]}
    assert {"Product Update - Feb", "Re-engagement - Q1"} <= flagged
    assert "Onboarding - Welcome" not in flagged


def test_results_summary_reports_missing_columns():
    r = checks.summarise_results("campaign,sent\nA,10\n")
    assert "missing columns" in r["error"]


def test_sfmc_extract_html_from_asset_payload():
    asset = {"name": "Q1 Webinar", "views": {"html": {"content": "<p>Hi</p>"}}}
    assert extract_html(asset) == "<p>Hi</p>"


def test_sfmc_fetch_email_with_mocked_api(monkeypatch):
    import httpx
    from preflight import sfmc

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/token":
            assert request.url.host == "acme.auth.marketingcloudapis.com"
            return httpx.Response(200, json={"access_token": "tok"})
        assert request.headers["Authorization"] == "Bearer tok"
        assert request.url.path == "/asset/v1/content/assets/42"
        return httpx.Response(200, json={
            "name": "Q1 Webinar",
            "views": {"html": {"content": CLEAN},
                      "subjectline": {"content": "Your Q1 webinar seat"},
                      "preheader": {"content": "Live on 12 February"}},
        })

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setenv("SFMC_SUBDOMAIN", "acme")
    monkeypatch.setenv("SFMC_CLIENT_ID", "id")
    monkeypatch.setenv("SFMC_CLIENT_SECRET", "secret")
    email = sfmc.fetch_email(42)
    assert email["name"] == "Q1 Webinar" and email["subject"] == "Your Q1 webinar seat"
    assert checks.preflight(email["html"])["verdict"] == "PASS"


def test_sfmc_not_configured(monkeypatch):
    from preflight import sfmc
    import pytest
    for k in ("SFMC_SUBDOMAIN", "SFMC_CLIENT_ID", "SFMC_CLIENT_SECRET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(sfmc.SFMCConfigError):
        sfmc.fetch_email(1)
