"""Gets live public data from ASSIST through Bright Data's Browser API.

ASSIST needs a token that the site gives out when its page loads, so we open the
real ASSIST page in a remote browser and capture the data request the page makes.
Every function returns {"status": ..., "data": ...}.
Status is one of: ok, slow, blocked, empty, error.
"""
import os
import time
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

load_dotenv()
BROWSER_WS = os.getenv("BROWSER_WS", "")


def _capture_once(page_url, match, timeout_ms=60000):
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


RETRIES = 3          # attempts per fetch; the remote browser sometimes stalls once and works the next time
BACKOFF = (4, 10)    # seconds to wait before attempt 2 and 3


def _retry(fn, *args, **kw):
    """Run a fetch up to RETRIES times when it is slow or errors. blocked/empty/ok are final."""
    out = None
    for i in range(RETRIES):
        out = fn(*args, **kw)
        if out["status"] not in ("slow", "error") or "BROWSER_WS is not configured" in out.get("detail", ""):
            return out
        if i < RETRIES - 1:
            time.sleep(BACKOFF[min(i, len(BACKOFF) - 1)])
    out["attempts"] = RETRIES
    return out


def capture_json(page_url, match, timeout_ms=60000):
    return _retry(_capture_once, page_url, match, timeout_ms)


def fetch_via_page(api_url, start_url="https://assist.org/", timeout_ms=60000):
    return _retry(_via_page_once, api_url, start_url, timeout_ms)


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


def _via_page_once(api_url, start_url="https://assist.org/", timeout_ms=60000):
    """Open assist.org in the remote browser, then call an ASSIST API URL from inside that page, so the call
    carries whatever the site gave the browser (cookies). Same return shape as capture_json."""
    if not BROWSER_WS:
        return {"status": "error", "data": None,
                "detail": "BROWSER_WS is not configured. Add it to your .env file to enable live ASSIST fetching."}
    script = """async (u) => {
        const r = await fetch(u, {headers: {'Accept': 'application/json'}});
        let body = null, text = "";
        try { text = await r.text(); body = JSON.parse(text); } catch (e) {}
        return {status: r.status, body: body, text: (text || "").slice(0, 300)};
    }"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(BROWSER_WS)
            try:
                page = browser.new_page()
                page.goto(start_url, timeout=timeout_ms)
                res = page.evaluate(script, api_url)
                if res["status"] != 200:
                    return {"status": "blocked", "data": None, "code": res["status"],
                            "detail": f"ASSIST answered {res['status']}: {res.get('text') or 'no message'}"}
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
    # ASSIST's own page for this college's Cal-GETC list. The page asks for the list itself (the same way the
    # majors are captured), which avoids the 400 we got when calling the address by hand.
    page = page_url or (f"https://assist.org/transfer/results?year={year}&institution={institution}"
                        "&type=CALGETC&view=transferability")
    out = capture_json(page, lambda u: "/api/transferability/courses" in u and "CALGETC" in u)
    if out["status"] == "ok" or page_url:
        return out
    out2 = fetch_via_page(api_url)          # last resort: call it from inside an assist.org page
    if out2["status"] == "ok":
        return out2
    out["detail"] = (out.get("detail") or "") + f" | direct call: {out2.get('code') or out2['status']}"
    return out

def fetch_institutions():
    """Every college and university in ASSIST (https://assist.org/api/institutions), as the raw list.
    The ASSIST home page asks for this list itself, so the page is opened and that answer is captured. If that
    does not work, the same address is called from inside the page."""
    out = capture_json("https://assist.org/", lambda u: u.split("?")[0].rstrip("/").endswith("/api/institutions"))
    if out["status"] != "ok":
        out = fetch_via_page("https://assist.org/api/institutions")
    if out["status"] != "ok":
        return out
    data = out["data"]
    if not isinstance(data, list) or not data:
        return {"status": "empty", "data": None}
    return {"status": "ok", "data": data}