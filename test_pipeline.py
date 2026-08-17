"""
Quick smoke tests for the research pipeline.

Usage:
  python test_pipeline.py          # run all checks
  python test_pipeline.py --quick  # skip verify (no Groq calls)
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL {msg}")


def check_env() -> bool:
    print("1. Environment (.env)")
    from dotenv import load_dotenv
    import os

    load_dotenv(ROOT / ".env")
    required = ["GROQ_API_KEY", "TAVILY_API_KEY", "FIRECRAWL_API_KEY"]
    all_ok = True
    for key in required:
        if os.getenv(key):
            ok(key)
        else:
            fail(f"{key} missing")
            all_ok = False
    return all_ok


def check_apps() -> bool:
    print("2. App list")
    apps = json.load(open(ROOT / "apps.json", encoding="utf-8"))
    if len(apps) == 100:
        ok(f"100 apps in apps.json")
        return True
    fail(f"Expected 100 apps, got {len(apps)}")
    return False


def check_results() -> bool:
    print("3. Research results (data/results_v1.json)")
    path = ROOT / "data" / "results_v1.json"
    if not path.exists():
        fail("results_v1.json not found — run: python research_agent.py")
        return False
    data = json.load(open(path, encoding="utf-8"))
    ids = {r["id"] for r in data}
    failed = [
        r["name"]
        for r in data
        if r.get("one_liner") == "Research failed" or "agent error" in str(r.get("blocker", ""))
    ]
    ok(f"{len(data)} rows, ids 1–{max(ids)}")
    if len(ids) == 100:
        ok("All 100 app IDs present")
    else:
        fail(f"Missing IDs: {set(range(1, 101)) - ids}")
    required_fields = ["auth_methods", "self_serve", "buildability", "evidence_urls"]
    bad = [r["name"] for r in data if not all(r.get(f) for f in required_fields[:3])]
    if bad:
        fail(f"Rows missing fields: {bad[:5]}")
    else:
        ok("Required fields present on all rows")
    if failed:
        print(f"  WARN {len(failed)} failed rows: {failed[:5]}...")
    return len(ids) == 100


def check_html() -> bool:
    print("4. Case study HTML")
    path = ROOT / "case-study" / "index.html"
    if not path.exists():
        fail("case-study/index.html missing — run: python generate_html.py")
        return False
    text = path.read_text(encoding="utf-8")
    checks = [
        ("Headline patterns", "Headline patterns" in text),
        ("Full matrix table", "id=\"matrix\"" in text),
        ("Verification section", "Verification" in text),
        ("Agent workflow", "research_agent.py" in text),
    ]
    all_ok = True
    for label, passed in checks:
        if passed:
            ok(label)
        else:
            fail(label)
            all_ok = False
    ok(f"File size {path.stat().st_size // 1024} KB")
    return all_ok


def check_apis_quick() -> bool:
    print("5. API connectivity (quick)")
    import os
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    all_ok = True

    try:
        from tavily import TavilyClient
        c = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
        r = c.search("GitHub API docs", max_results=1)
        ok(f"Tavily ({len(r.get('results', []))} result)")
    except Exception as e:
        fail(f"Tavily: {e}")
        all_ok = False

    try:
        from groq import Groq
        c = Groq(api_key=os.getenv("GROQ_API_KEY"))
        from config import GROQ_MODEL
        r = c.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": "Reply: OK"}],
            max_tokens=5,
        )
        ok(f"Groq model {GROQ_MODEL}")
    except Exception as e:
        fail(f"Groq: {e}")
        all_ok = False

    return all_ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true", help="Skip API connectivity tests")
    args = parser.parse_args()

    print("Composio research pipeline — tests\n")
    results = [
        check_env(),
        check_apps(),
        check_results(),
        check_html(),
    ]
    if not args.quick:
        results.append(check_apis_quick())

    print()
    if all(results):
        print("All checks passed.")
        print("\nOpen case study: case-study\\index.html")
        print("Or: start case-study\\index.html in your browser")
        sys.exit(0)
    print("Some checks failed — see above.")
    sys.exit(1)


if __name__ == "__main__":
    main()
