"""
Research agent: discovers docs (Tavily), scrapes pages (Firecrawl),
extracts structured fields (Groq), saves to data/results_v1.json.

Usage:
  python research_agent.py              # all 100 apps
  python research_agent.py --limit 5    # smoke test
  python research_agent.py --resume     # skip already-researched apps
"""

import argparse
import time
from datetime import datetime, timezone

from groq import Groq
from tavily import TavilyClient

from config import (
    DATA_DIR,
    FIRECRAWL_API_KEY,
    GROQ_API_KEY,
    groq_chat,
    META,
    RESULTS_V1,
    TAVILY_API_KEY,
    ensure_dirs,
    load_apps,
    load_json,
    parse_json_from_llm,
    retry,
    save_json,
    sync_supabase,
)

EXTRACTION_PROMPT = """You are researching whether an app can become an AI agent toolkit.

App: {name}
Category: {category}
Website hint: {hint}

Below is scraped documentation and search snippets. Extract facts ONLY from this text.
If something is unclear, say unknown and lower confidence. Do not invent URLs.

CONTENT:
{content}

Return a single JSON object with these fields:
- one_liner: string (what it does in one line)
- auth_methods: array of strings from: OAuth2, API key, Basic, token, other
- self_serve: one of: self_serve, trial, paid_plan, admin_approval, partnership, contact_sales, no_api
- self_serve_detail: string
- api_type: one of: REST, GraphQL, both, SDK_only, CLI, none, unknown
- api_breadth: one of: broad, moderate, narrow, none, unknown
- mcp_exists: boolean
- mcp_detail: string (URL or note)
- buildability: one of: ready, partial, blocked, no_api
- blocker: string (main blocker or "none")
- evidence_urls: array of URLs from the content that support your answers
- confidence: float 0-1

JSON only, no markdown."""


def tavily_search(client: TavilyClient, name: str, hint: str) -> list[dict]:
    queries = [
        f"{name} API documentation authentication developer",
        f"{name} REST API OAuth API key {hint}",
    ]
    results = []
    seen_urls = set()
    for q in queries:
        resp = client.search(query=q, search_depth="basic", max_results=5)
        for r in resp.get("results", []):
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                results.append(
                    {
                        "url": url,
                        "title": r.get("title", ""),
                        "snippet": r.get("content", "")[:800],
                    }
                )
        time.sleep(0.3)
    return results[:8]


def firecrawl_scrape(url: str, max_chars: int = 4000) -> str:
    if not FIRECRAWL_API_KEY:
        return ""
    try:
        from firecrawl import FirecrawlApp

        app = FirecrawlApp(api_key=FIRECRAWL_API_KEY)
        result = app.scrape_url(url, params={"formats": ["markdown"]})
        md = ""
        if isinstance(result, dict):
            md = result.get("markdown") or result.get("data", {}).get("markdown") or ""
        return md[:max_chars]
    except Exception as e:
        return f"[scrape failed: {e}]"


def groq_extract(client: Groq, app: dict, content: str) -> dict:
    prompt = EXTRACTION_PROMPT.format(
        name=app["name"],
        category=app["category"],
        hint=app.get("hint", app.get("website", "")),
        content=content[:12000],
    )
    text = groq_chat(client, [{"role": "user", "content": prompt}])
    return parse_json_from_llm(text)


def heuristic_extract(app: dict, search_results: list, scraped_parts: list) -> dict:
    """Rule-based fallback when LLM rate-limited — uses Tavily snippets + scraped text."""
    text = " ".join(
        [r.get("snippet", "") for r in search_results]
        + scraped_parts
    ).lower()
    auth = []
    if "oauth" in text:
        auth.append("OAuth2")
    if "api key" in text or "api-key" in text or "apikey" in text:
        auth.append("API key")
    if "basic auth" in text or "basic authentication" in text:
        auth.append("Basic")
    if "bearer" in text or "access token" in text or "personal access token" in text:
        auth.append("token")
    if not auth:
        auth = ["unknown"]

    self_serve = "unknown"
    if "contact sales" in text or "contact us" in text:
        self_serve = "contact_sales"
    elif "partner" in text:
        self_serve = "partnership"
    elif "free trial" in text or "trial" in text:
        self_serve = "trial"
    elif "sign up" in text or "developer account" in text or "self-serve" in text:
        self_serve = "self_serve"
    elif "paid" in text or "subscription" in text:
        self_serve = "paid_plan"

    api_type = "unknown"
    if "graphql" in text and "rest" in text:
        api_type = "both"
    elif "graphql" in text:
        api_type = "GraphQL"
    elif "rest api" in text or "restful" in text:
        api_type = "REST"
    elif "cli" in text:
        api_type = "CLI"
    elif "no api" in text:
        api_type = "none"

    mcp_exists = "mcp" in text and "server" in text
    buildability = "partial"
    blocker = "none"
    if self_serve in ("partnership", "contact_sales", "admin_approval"):
        buildability = "blocked"
        blocker = f"{self_serve} gate"
    elif api_type == "none":
        buildability = "no_api"
        blocker = "no public API"
    elif self_serve in ("self_serve", "trial"):
        buildability = "ready"

    urls = [r["url"] for r in search_results[:3]]
    return {
        "one_liner": f"{app['name']} — {app['category']} (heuristic extract)",
        "auth_methods": auth,
        "self_serve": self_serve,
        "self_serve_detail": "Heuristic from search snippets (LLM rate-limited)",
        "api_type": api_type,
        "api_breadth": "unknown",
        "mcp_exists": mcp_exists,
        "mcp_detail": "MCP mentioned in docs" if mcp_exists else "",
        "buildability": buildability,
        "blocker": blocker,
        "evidence_urls": urls,
        "confidence": 0.55,
        "extraction_method": "heuristic",
    }


def research_one(
    app: dict, tavily: TavilyClient, groq: Groq, use_heuristic_only: bool = False
) -> dict:
    print(f"  Searching Tavily...")
    search_results = retry(lambda: tavily_search(tavily, app["name"], app.get("hint", "")))

    content_parts = ["=== SEARCH SNIPPETS ==="]
    evidence_urls = []
    for r in search_results:
        content_parts.append(f"URL: {r['url']}\nTitle: {r['title']}\n{r['snippet']}")
        evidence_urls.append(r["url"])

    # Scrape top 2 doc-like URLs (save Firecrawl credits)
    scrape_targets = [
        r["url"]
        for r in search_results
        if any(
            x in r["url"].lower()
            for x in ["docs", "developer", "api", "rest", "graphql", "help", "learn"]
        )
    ][:2]
    if not scrape_targets and search_results:
        scrape_targets = [search_results[0]["url"]]

    scraped_parts = []
    for url in scrape_targets:
        print(f"  Scraping {url[:60]}...")
        md = retry(lambda: firecrawl_scrape(url))
        if md and not md.startswith("[scrape failed"):
            content_parts.append(f"=== SCRAPED: {url} ===\n{md}")
            scraped_parts.append(md)
        time.sleep(0.5)

    full_content = "\n\n".join(content_parts)

    if use_heuristic_only:
        print("  Extracting with heuristics (LLM skipped)...")
        extracted = heuristic_extract(app, search_results, scraped_parts)
    else:
        print(f"  Extracting with Groq...")
        try:
            extracted = retry(lambda: groq_extract(groq, app, full_content))
            extracted["extraction_method"] = "llm"
        except Exception as e:
            print(f"  LLM failed ({e}), falling back to heuristics...")
            extracted = heuristic_extract(app, search_results, scraped_parts)

    if not extracted.get("evidence_urls"):
        extracted["evidence_urls"] = evidence_urls[:3]

    record = {
        **app,
        **extracted,
        "research_pass": 1,
        "researched_at": datetime.now(timezone.utc).isoformat(),
        "search_urls": [r["url"] for r in search_results],
    }
    return record


def main():
    parser = argparse.ArgumentParser(description="Research 100 apps for Composio assignment")
    parser.add_argument("--limit", type=int, default=0, help="Max apps to research")
    parser.add_argument("--resume", action="store_true", help="Skip apps already in results_v1")
    parser.add_argument("--retry-failed", action="store_true", help="Re-research failed rows only")
    parser.add_argument("--fresh", action="store_true", help="Ignore existing results_v1")
    parser.add_argument("--heuristic-only", action="store_true", help="Skip LLM, use rule-based extraction")
    parser.add_argument("--ids", type=str, default="", help="Comma-separated app IDs only")
    args = parser.parse_args()

    if not GROQ_API_KEY or not TAVILY_API_KEY:
        raise SystemExit("Set GROQ_API_KEY and TAVILY_API_KEY in .env")

    ensure_dirs()
    apps = load_apps()
    existing = [] if args.fresh else load_json(RESULTS_V1, default=[])
    results_by_id = {r["id"]: r for r in existing}
    done_ids = set(results_by_id.keys()) if args.resume else set()

    if args.retry_failed:
        failed_ids = {
            r["id"]
            for r in existing
            if r.get("one_liner") == "Research failed"
            or "agent error" in str(r.get("blocker", ""))
            or (r.get("confidence") == 0.0 and "429" in str(r.get("self_serve_detail", "")))
        }
        apps = [a for a in load_apps() if a["id"] in failed_ids]
        for fid in failed_ids:
            results_by_id.pop(fid, None)
        done_ids = set()

    if args.ids:
        id_set = {int(x.strip()) for x in args.ids.split(",")}
        apps = [a for a in apps if a["id"] in id_set]

    if args.limit:
        apps = apps[:args.limit]

    apps = [a for a in apps if a["id"] not in done_ids]

    tavily = TavilyClient(api_key=TAVILY_API_KEY)
    groq = Groq(api_key=GROQ_API_KEY)

    def save_results() -> None:
        merged = sorted(results_by_id.values(), key=lambda x: x["id"])
        save_json(RESULTS_V1, merged)

    start = time.time()

    print(f"Researching {len(apps)} apps...")
    for i, app in enumerate(apps, 1):
        print(f"\n[{i}/{len(apps)}] {app['name']} ({app['category']})")
        try:
            record = research_one(app, tavily, groq, use_heuristic_only=args.heuristic_only)
            results_by_id[record["id"]] = record
            sync_supabase(record)
            save_results()
            print(f"  -> {record.get('buildability')} | auth={record.get('auth_methods')} | conf={record.get('confidence')}")
        except Exception as e:
            print(f"  FAILED: {e}")
            results_by_id[app["id"]] = {
                **app,
                "one_liner": "Research failed",
                "auth_methods": ["unknown"],
                "self_serve": "unknown",
                "self_serve_detail": str(e),
                "api_type": "unknown",
                "api_breadth": "unknown",
                "mcp_exists": False,
                "mcp_detail": "",
                "buildability": "blocked",
                "blocker": f"agent error: {e}",
                "evidence_urls": [],
                "confidence": 0.0,
                "research_pass": 1,
                "researched_at": datetime.now(timezone.utc).isoformat(),
            }
            save_results()
        time.sleep(1)

    elapsed = time.time() - start
    meta = load_json(META, default={})
    meta["pass1_completed_at"] = datetime.now(timezone.utc).isoformat()
    meta["pass1_count"] = len(results_by_id)
    meta["pass1_elapsed_sec"] = round(elapsed, 1)
    save_json(META, meta)

    print(f"\nDone. {len(results_by_id)} apps in {RESULTS_V1} ({elapsed:.0f}s)")


if __name__ == "__main__":
    main()
