"""Gets live public data from ASSIST through Bright Data's Browser API.

ASSIST needs a token that the site gives out when its page loads, so we open the
real ASSIST page in a remote browser and capture the data request the page makes.
Every function returns {"status": ..., "data": ...}.
Status is one of: ok, slow, blocked, empty, error.
"""
import os
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

load_dotenv()
BROWSER_WS = os.getenv("BROWSER_WS", "")


def capture_json(page_url, match, timeout_ms=60000):
    """Open a page in the Bright Data browser and return the JSON of the first
    response whose URL passes match(url)."""
    if not BROWSER_WS:
        return {"status": "error", "data": None,
                "detail": "BROWSER_WS is not configured. Add it to your .env file to enable live ASSIST fetching."}
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(BROWSER_WS)
            try:
                page = browser.new_page()
                with page.expect_response(lambda r: match(r.url), timeout=timeout_ms) as info:
                    page.goto(page_url, timeout=timeout_ms)
                resp = info.value
                if resp.status != 200:
                    return {"status": "blocked", "data": None, "code": resp.status}
                return {"status": "ok", "data": resp.json()}
            finally:
                browser.close()
    except PWTimeout:
        return {"status": "slow", "data": None}
    except Exception as e:
        return {"status": "error", "data": None, "detail": str(e)[:200]}


def fetch_majors(sending=51, receiving=7, year=77):
    """List of majors for one school pair. Defaults: Foothill (51) -> UCSD (7), 2026-2027 (77)."""
    page_url = (f"https://assist.org/transfer/results?year={year}&institution={sending}"
                f"&agreement={receiving}&agreementType=to&viewAgreementsOptions=true"
                f"&view=agreement&viewBy=major")
    out = capture_json(
        page_url,
        lambda u: "/api/agreements" in u and "categoryCode=major" in u,
    )
    if out["status"] != "ok":
        return out
    reports = out["data"].get("reports") or []
    majors = [{"label": r["label"], "key": r["key"]} for r in reports]
    if not majors:
        return {"status": "empty", "data": None}
    return {"status": "ok", "data": majors}


def fetch_agreement(key):
    """Agreement for one major. `key` looks like 77/51/to/7/Major/<major-id>."""
    year, sending, _to, receiving, _major, _id = key.split("/")
    page_url = (f"https://assist.org/transfer/results?year={year}&institution={sending}"
                f"&agreement={receiving}&agreementType=to&viewAgreementsOptions=true"
                f"&view=agreement&viewBy=major&viewSendingAgreements=false"
                f"&viewByKey={key.replace('/', '%2F')}")
    out = capture_json(page_url, lambda u: "/api/articulation/Agreements" in u)
    if out["status"] != "ok":
        return out
    body = out["data"]
    if not body.get("isSuccessful") or not body.get("result"):
        return {"status": "empty", "data": None}
    return {"status": "ok", "data": body["result"]}


def fetch_via_page(api_url, start_url="https://assist.org/", timeout_ms=60000):
    """Open assist.org in the remote browser, then call an ASSIST API URL from inside that page, so the call
    carries whatever the site gave the browser (cookies). Same return shape as capture_json."""
    if not BROWSER_WS:
        return {"status": "error", "data": None,
                "detail": "BROWSER_WS is not configured. Add it to your .env file to enable live ASSIST fetching."}
    script = """async (u) => {
        const r = await fetch(u, {headers: {'Accept': 'application/json'}});
        let body = null;
        try { body = await r.json(); } catch (e) {}
        return {status: r.status, body: body};
    }"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(BROWSER_WS)
            try:
                page = browser.new_page()
                page.goto(start_url, timeout=timeout_ms)
                res = page.evaluate(script, api_url)
                if res["status"] != 200:
                    return {"status": "blocked", "data": None, "code": res["status"]}
                if res["body"] is None:
                    return {"status": "error", "data": None, "detail": "ASSIST answered, but not with JSON."}
                return {"status": "ok", "data": res["body"]}
            finally:
                browser.close()
    except PWTimeout:
        return {"status": "slow", "data": None}
    except Exception as e:
        return {"status": "error", "data": None, "detail": str(e)[:200]}


CALGETC_API = ("https://assist.org/api/transferability/courses"
               "?institutionId={institution}&academicYearId={year}&listType=CALGETC")


def fetch_calgetc(institution=51, year=77, page_url=None):
    """ASSIST's Cal-GETC course list for one community college and year. Defaults: Foothill (51), 2026-2027 (77).

    By default the API is called from inside an assist.org page. If ASSIST refuses that (status "blocked"),
    pass page_url: the ASSIST page that makes this request on its own (find it in your browser's Network tab)."""
    api_url = CALGETC_API.format(institution=institution, year=year)
    if page_url:
        return capture_json(page_url, lambda u: "/api/transferability/courses" in u and "CALGETC" in u)
    return fetch_via_page(api_url)