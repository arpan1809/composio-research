"""
Verification agent: re-checks evidence URLs, runs keyword validation,
optional Playwright fetch, and re-extracts low-confidence rows.

Usage:
  python verify_agent.py
  python verify_agent.py --sample 20   # verify random sample for human audit
  python verify_agent.py --playwright  # browser fetch for evidence pages
"""

import argparse
import random
import re
import time
from datetime import datetime, timezone

import requests
from groq import Groq

from config import (
    GROQ_API_KEY,
    groq_chat,
    META,
    RESULTS_V1,
    RESULTS_V2,
    VERIFICATION,
    ensure_dirs,
    load_json,
    parse_json_from_llm,
    retry,
    save_json,
    sync_supabase,
)

VERIFY_PROMPT = """Compare ORIGINAL research vs FRESH page text for app "{name}".

ORIGINAL: {original}

FRESH (excerpt): {fresh}

Reply with ONLY this JSON (no markdown, no extra text):
{{"overall":"correct|partial|wrong|unverifiable","verification_note":"one short sentence"}}

Rules:
- correct = original auth/self_serve/buildability match the page
- partial = mostly right, minor gaps
- wrong = clear contradiction
- unverifiable = page is empty, JS-only, or unrelated to the app
"""


def keyword_verdict(record: dict, kw_checks: list[dict], fresh: str) -> dict:
    """Fallback verifier when LLM JSON fails — uses keyword hits only."""
    if fresh.startswith("[fetch error") or fresh.startswith("[playwright error"):
        return {
            "overall": "unverifiable",
            "field_checks": [],
            "verification_note": "Could not fetch evidence page",
        }
    if len(fresh.strip()) < 200:
        return {
            "overall": "unverifiable",
            "field_checks": [],
            "verification_note": "Page content too short or JS-rendered",
        }
    if not kw_checks:
        return {
            "overall": "unverifiable",
            "field_checks": [],
            "verification_note": "No keyword checks available",
        }
    found = sum(1 for c in kw_checks if c.get("keyword_found"))
    total = len(kw_checks)
    if found == total:
        overall = "correct"
    elif found > 0:
        overall = "partial"
    else:
        overall = "unverifiable"
    return {
        "overall": overall,
        "field_checks": [{"field": c.get("field"), "verdict": c.get("verdict"), "note": c.get("value")} for c in kw_checks],
        "verification_note": f"Keyword check: {found}/{total} auth/gate terms found on page",
    }


AUTH_KEYWORDS = {
    "OAuth2": ["oauth", "oauth2", "openid"],
    "API key": ["api key", "api-key", "apikey", "x-api-key"],
    "Basic": ["basic auth", "username and password"],
    "token": ["bearer token", "access token", "personal access token", "pat"],
}


def fetch_url_text(url: str, timeout: int = 15) -> str:
    headers = {"User-Agent": "ComposioResearchBot/1.0 (assignment verification)"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text)
        return text[:6000]
    except Exception as e:
        return f"[fetch error: {e}]"


def playwright_fetch(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            text = page.inner_text("body")
            browser.close()
            return text[:6000]
    except Exception as e:
        return f"[playwright error: {e}]"


def keyword_check(record: dict, page_text: str) -> list[dict]:
    checks = []
    page_lower = page_text.lower()
    auth = record.get("auth_methods") or []
    for method in auth:
        if method == "unknown" or method == "other":
            continue
        keywords = AUTH_KEYWORDS.get(method, [method.lower()])
        found = any(k in page_lower for k in keywords)
        checks.append(
            {
                "field": "auth_methods",
                "value": method,
                "keyword_found": found,
                "verdict": "correct" if found else "unverifiable",
            }
        )
    gate = (record.get("self_serve") or "").lower()
    gate_keywords = {
        "partnership": ["partner", "partnership"],
        "contact_sales": ["contact sales", "contact us", "sales team"],
        "paid_plan": ["paid plan", "subscription", "pricing"],
        "admin_approval": ["admin approval", "administrator"],
    }
    if gate in gate_keywords:
        found = any(k in page_lower for k in gate_keywords[gate])
        checks.append(
            {
                "field": "self_serve",
                "value": gate,
                "keyword_found": found,
                "verdict": "correct" if found else "unverifiable",
            }
        )
    return checks


def pick_evidence_url(record: dict) -> str | None:
    """Prefer official docs URLs over third-party aggregators."""
    urls = record.get("evidence_urls") or record.get("search_urls") or []
    bad_hosts = ("github.com/api-evangelist", "apitracker.io", "apis.io/", "publicapis.io")
    good = [
        u for u in urls
        if not any(b in u.lower() for b in bad_hosts)
        and any(x in u.lower() for x in ("docs", "developer", "api.", "/api", "help.", "learn."))
    ]
    if good:
        return good[0]
    return urls[0] if urls else None


def groq_verify(client: Groq, record: dict, fresh: str) -> dict:
    original = {
        k: record.get(k)
        for k in [
            "auth_methods",
            "self_serve",
            "api_type",
            "buildability",
            "blocker",
        ]
    }
    prompt = VERIFY_PROMPT.format(
        name=record["name"],
        original=str(original),
        fresh=fresh[:4000],
    )
    text = groq_chat(client, [{"role": "user", "content": prompt}], max_tokens=300)
    return parse_json_from_llm(text)


def verify_one(record: dict, groq: Groq, use_playwright: bool, no_llm: bool = False) -> tuple[dict, dict]:
    url = pick_evidence_url(record)
    if not url:
        return record, {
            "app_id": record["id"],
            "app_name": record["name"],
            "overall": "unverifiable",
            "note": "No evidence URLs",
            "field_checks": [],
        }

    print(f"  Fetching {url[:70]}...")
    if use_playwright:
        fresh = retry(lambda: playwright_fetch(url))
    else:
        fresh = retry(lambda: fetch_url_text(url))

    kw_checks = keyword_check(record, fresh)

    if no_llm:
        llm_verify = keyword_verdict(record, kw_checks, fresh)
        llm_verify["method"] = "keyword_only"
    else:
        try:
            llm_verify = groq_verify(groq, record, fresh)
            llm_verify["method"] = "llm"
        except Exception as e:
            print(f"  LLM verify failed ({e}), using keyword fallback...")
            llm_verify = keyword_verdict(record, kw_checks, fresh)
            llm_verify["method"] = "keyword_fallback"
            llm_verify["llm_error"] = str(e)[:120]

    verification = {
        "app_id": record["id"],
        "app_name": record["name"],
        "evidence_url": url,
        "overall": llm_verify.get("overall", "unverifiable"),
        "field_checks": llm_verify.get("field_checks", []),
        "keyword_checks": kw_checks,
        "verification_note": llm_verify.get("verification_note", ""),
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }

    updated = dict(record)
    corrected = llm_verify.get("corrected") or {}
    if corrected:
        updated.update(corrected)
    updated["research_pass"] = 2
    updated["verified_at"] = verification["verified_at"]
    updated["verification_overall"] = verification["overall"]

    if verification["overall"] in ("correct", "partial") and updated.get("confidence", 0) < 0.85:
        updated["confidence"] = min(0.85, updated.get("confidence", 0) + 0.1)

    return updated, verification


def main():
    parser = argparse.ArgumentParser(description="Verify research results")
    parser.add_argument("--sample", type=int, default=0, help="Only verify N apps (for audit)")
    parser.add_argument("--playwright", action="store_true", help="Use Playwright for fetches")
    parser.add_argument("--low-confidence", action="store_true", help="Only verify confidence < 0.75")
    parser.add_argument("--no-llm", action="store_true", help="Keyword-only verification (no Groq, always works)")
    args = parser.parse_args()

    if not GROQ_API_KEY:
        raise SystemExit("Set GROQ_API_KEY in .env")

    ensure_dirs()
    results = load_json(RESULTS_V1, default=[])
    if not results:
        raise SystemExit(f"No results in {RESULTS_V1}. Run research_agent.py first.")

    to_verify = list(results)
    if args.low_confidence:
        to_verify = [r for r in to_verify if (r.get("confidence") or 0) < 0.75]
    if args.sample:
        random.seed(42)
        to_verify = random.sample(to_verify, min(args.sample, len(to_verify)))

    groq = Groq(api_key=GROQ_API_KEY)
    verified_results = []
    verifications = []

    print(f"Verifying {len(to_verify)} apps...")
    for i, record in enumerate(to_verify, 1):
        print(f"\n[{i}/{len(to_verify)}] {record['name']}")
        try:
            updated, v = verify_one(record, groq, args.playwright, no_llm=args.no_llm)
            verified_results.append(updated)
            verifications.append(v)
            sync_supabase(updated)
            print(f"  -> {v['overall']} | {v.get('verification_note', '')[:80]}")
        except Exception as e:
            print(f"  FAILED: {e}")
            verifications.append(
                {
                    "app_id": record["id"],
                    "app_name": record["name"],
                    "overall": "error",
                    "note": str(e),
                }
            )
            verified_results.append(record)
        time.sleep(0.5)

    # Merge: keep unverified pass-1 rows + replace verified ones
    verified_ids = {r["id"] for r in verified_results}
    merged = [r for r in results if r["id"] not in verified_ids] + verified_results
    merged.sort(key=lambda x: x["id"])

    save_json(RESULTS_V2, merged)
    save_json(VERIFICATION, verifications)

    correct = sum(1 for v in verifications if v.get("overall") == "correct")
    partial = sum(1 for v in verifications if v.get("overall") == "partial")
    wrong = sum(1 for v in verifications if v.get("overall") == "wrong")
    unv = len(verifications) - correct - partial - wrong

    meta = load_json(META, default={})
    meta["pass2_completed_at"] = datetime.now(timezone.utc).isoformat()
    meta["verification_sample_size"] = len(verifications)
    meta["verification_stats"] = {
        "correct": correct,
        "partial": partial,
        "wrong": wrong,
        "unverifiable": unv,
        "accuracy_pct": round((correct + partial * 0.5) / max(len(verifications), 1) * 100, 1),
    }
    save_json(META, meta)

    print(f"\nVerification done. {RESULTS_V2}")
    print(f"Stats: correct={correct} partial={partial} wrong={wrong} unverifiable={unv}")
    print(f"Estimated accuracy: {meta['verification_stats']['accuracy_pct']}%")


if __name__ == "__main__":
    main()
