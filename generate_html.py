"""
Generate self-contained HTML case study from research results.

Usage:
  python generate_html.py
  python generate_html.py --input data/results_v2.json
"""

import argparse
import html
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from config import (
    CASE_STUDY_DIR,
    META,
    RESULTS_V1,
    RESULTS_V2,
    VERIFICATION,
    ensure_dirs,
    load_json,
)


def pick_results(input_path: str | None) -> list:
    if input_path:
        return load_json(Path(input_path), default=[])
    v1 = load_json(RESULTS_V1, default=[])
    v2 = load_json(RESULTS_V2, default=[])
    if v2 and len(v2) >= len(v1):
        return v2
    return v1 or v2


def compute_patterns(results: list) -> dict:
    auth_counter = Counter()
    self_serve_counter = Counter()
    buildability_counter = Counter()
    api_type_counter = Counter()
    category_auth = defaultdict(Counter)
    category_self_serve = defaultdict(Counter)
    blockers = Counter()
    mcp_count = 0
    easy_wins = []
    outreach = []

    for r in results:
        for a in r.get("auth_methods") or ["unknown"]:
            auth_counter[a] += 1
        ss = r.get("self_serve") or "unknown"
        self_serve_counter[ss] += 1
        b = r.get("buildability") or "unknown"
        buildability_counter[b] += 1
        api_type_counter[r.get("api_type") or "unknown"] += 1
        cat = r.get("category") or "Other"
        for a in r.get("auth_methods") or ["unknown"]:
            category_auth[cat][a] += 1
        category_self_serve[cat][ss] += 1
        blocker = (r.get("blocker") or "").strip()
        if blocker and blocker.lower() != "none":
            blockers[blocker[:80]] += 1
        if r.get("mcp_exists"):
            mcp_count += 1
        if b == "ready" and ss in ("self_serve", "trial"):
            easy_wins.append(r["name"])
        if ss in ("partnership", "contact_sales", "admin_approval") or b == "blocked":
            outreach.append(r["name"])

    return {
        "auth_counter": auth_counter,
        "self_serve_counter": self_serve_counter,
        "buildability_counter": buildability_counter,
        "api_type_counter": api_type_counter,
        "category_auth": dict(category_auth),
        "category_self_serve": dict(category_self_serve),
        "blockers": blockers.most_common(8),
        "mcp_count": mcp_count,
        "easy_wins": easy_wins[:12],
        "outreach": outreach[:12],
        "total": len(results),
    }


def headline_patterns(p: dict) -> list[str]:
    lines = []
    auth = p["auth_counter"].most_common(3)
    if auth:
        lines.append(
            f"Auth: {auth[0][0]} leads ({auth[0][1]} apps), followed by "
            + ", ".join(f"{a[0]} ({a[1]})" for a in auth[1:3])
        )
    ss = p["self_serve_counter"]
    self_serve = ss.get("self_serve", 0) + ss.get("trial", 0)
    gated = sum(ss.get(k, 0) for k in ("partnership", "contact_sales", "admin_approval", "paid_plan"))
    lines.append(f"Self-serve/trial: ~{self_serve} apps vs gated/paid: ~{gated} apps")
    ready = p["buildability_counter"].get("ready", 0)
    blocked = p["buildability_counter"].get("blocked", 0)
    lines.append(f"Buildability: {ready} ready today, {blocked} blocked or no API")
    if p["blockers"]:
        lines.append(f"Top blocker: {p['blockers'][0][0]} ({p['blockers'][0][1]} apps)")
    lines.append(f"Existing MCP servers found: {p['mcp_count']} apps")
    return lines


def esc(s) -> str:
    return html.escape(str(s) if s is not None else "")


def badge_class(val: str, kind: str) -> str:
    good = {"ready", "self_serve", "trial", "correct", "REST", "broad"}
    warn = {"partial", "paid_plan", "moderate", "blocked", "admin_approval"}
    bad = {"wrong", "no_api", "partnership", "contact_sales"}
    if val in good:
        return "badge good"
    if val in warn:
        return "badge warn"
    if val in bad:
        return "badge bad"
    return "badge"


def render_table(results: list) -> str:
    rows = []
    for r in sorted(results, key=lambda x: x.get("id", 0)):
        auth = ", ".join(r.get("auth_methods") or [])
        urls = r.get("evidence_urls") or []
        url_links = " ".join(
            f'<a href="{esc(u)}" target="_blank" rel="noopener">docs</a>' for u in urls[:2]
        )
        rows.append(
            f"<tr>"
            f"<td>{r.get('id')}</td>"
            f"<td><strong>{esc(r.get('name'))}</strong></td>"
            f"<td>{esc(r.get('category'))}</td>"
            f"<td>{esc(r.get('one_liner', ''))[:100]}</td>"
            f"<td>{esc(auth)}</td>"
            f"<td><span class='{badge_class(r.get('self_serve',''), 'ss')}'>{esc(r.get('self_serve'))}</span></td>"
            f"<td>{esc(r.get('api_type'))} / {esc(r.get('api_breadth'))}</td>"
            f"<td><span class='{badge_class(r.get('buildability',''), 'b')}'>{esc(r.get('buildability'))}</span></td>"
            f"<td>{esc((r.get('blocker') or '')[:60])}</td>"
            f"<td>{'yes' if r.get('mcp_exists') else 'no'}</td>"
            f"<td>{r.get('confidence', 0):.0%}</td>"
            f"<td>{url_links}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def render_verification(verifications: list) -> str:
    if not verifications:
        return "<p>No verification run yet. Run <code>python verify_agent.py --sample 20</code>.</p>"
    rows = []
    for v in verifications:
        rows.append(
            f"<tr>"
            f"<td>{esc(v.get('app_name'))}</td>"
            f"<td><span class='{badge_class(v.get('overall',''), 'v')}'>{esc(v.get('overall'))}</span></td>"
            f"<td><a href='{esc(v.get('evidence_url',''))}' target='_blank'>evidence</a></td>"
            f"<td>{esc(v.get('verification_note', v.get('note', ''))[:120])}</td>"
            f"</tr>"
        )
    return "\n".join(rows)


def build_html(results: list, patterns: dict, meta: dict, verifications: list) -> str:
    headlines = headline_patterns(patterns)
    stats = meta.get("verification_stats", {})
    pass1 = meta.get("pass1_count", len(results))
    acc = stats.get("accuracy_pct", "—")

    auth_bars = ""
    for name, count in patterns["auth_counter"].most_common(8):
        pct = count / max(patterns["total"], 1) * 100
        auth_bars += f'<div class="bar-row"><span>{esc(name)}</span><div class="bar"><div style="width:{pct}%"></div></div><span>{count}</span></div>'

    build_bars = ""
    for name, count in patterns["buildability_counter"].most_common():
        pct = count / max(patterns["total"], 1) * 100
        build_bars += f'<div class="bar-row"><span>{esc(name)}</span><div class="bar"><div style="width:{pct}%"></div></div><span>{count}</span></div>'

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Composio App Research — 100 Apps Case Study</title>
  <style>
    :root {{
      --bg: #0f1419;
      --surface: #1a2332;
      --border: #2d3a4d;
      --text: #e7ecf3;
      --muted: #8b9cb3;
      --accent: #6366f1;
      --good: #22c55e;
      --warn: #f59e0b;
      --bad: #ef4444;
    }}
  * {{ box-sizing: border-box; }}
    body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); line-height: 1.5; margin: 0; }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 2rem 1.5rem; }}
    h1 {{ font-size: 1.75rem; margin: 0 0 0.5rem; }}
    h2 {{ font-size: 1.25rem; margin: 2rem 0 1rem; border-bottom: 1px solid var(--border); padding-bottom: 0.5rem; }}
    .subtitle {{ color: var(--muted); margin-bottom: 2rem; }}
    .hero {{ background: linear-gradient(135deg, #1e1b4b, #0f1419); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; margin-bottom: 2rem; }}
    .headlines {{ list-style: none; padding: 0; }}
    .headlines li {{ padding: 0.4rem 0; border-left: 3px solid var(--accent); padding-left: 1rem; margin-bottom: 0.5rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 1rem; }}
    .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 1rem; }}
    .card h3 {{ margin: 0 0 0.75rem; font-size: 0.95rem; color: var(--muted); }}
    .bar-row {{ display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.4rem; font-size: 0.85rem; }}
    .bar-row span:first-child {{ flex: 0 0 90px; }}
    .bar {{ flex: 1; height: 8px; background: var(--border); border-radius: 4px; overflow: hidden; }}
    .bar div {{ height: 100%; background: var(--accent); border-radius: 4px; }}
    .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }}
    .badge.good {{ background: rgba(34,197,94,0.2); color: var(--good); }}
    .badge.warn {{ background: rgba(245,158,11,0.2); color: var(--warn); }}
    .badge.bad {{ background: rgba(239,68,68,0.2); color: var(--bad); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
    th, td {{ border: 1px solid var(--border); padding: 0.5rem; text-align: left; vertical-align: top; }}
    th {{ background: var(--surface); position: sticky; top: 0; }}
    .table-wrap {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; }}
    code {{ background: var(--surface); padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.85em; }}
    a {{ color: #818cf8; }}
    .flow {{ font-family: monospace; font-size: 0.8rem; background: var(--surface); padding: 1rem; border-radius: 8px; overflow-x: auto; }}
    .stat {{ font-size: 2rem; font-weight: 700; color: var(--accent); }}
    .stat-label {{ font-size: 0.8rem; color: var(--muted); }}
    ul.compact {{ padding-left: 1.2rem; margin: 0; }}
    ul.compact li {{ margin-bottom: 0.25rem; }}
    #search {{ width: 100%; padding: 0.6rem; margin-bottom: 0.5rem; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>100-App API Research Case Study</h1>
    <p class="subtitle">Composio AI Product Ops take-home · Agent-built research with verification loops · Generated {generated}</p>

    <section class="hero">
      <h2 style="margin-top:0;border:none;">Headline patterns</h2>
      <ul class="headlines">
        {"".join(f"<li>{esc(h)}</li>" for h in headlines)}
      </ul>
    </section>

    <div class="grid">
      <div class="card">
        <div class="stat">{patterns['total']}</div>
        <div class="stat-label">Apps researched</div>
      </div>
      <div class="card">
        <div class="stat">{acc}%</div>
        <div class="stat-label">Verified accuracy (sample)</div>
      </div>
      <div class="card">
        <div class="stat">{patterns['mcp_count']}</div>
        <div class="stat-label">Apps with MCP</div>
      </div>
      <div class="card">
        <div class="stat">{patterns['buildability_counter'].get('ready', 0)}</div>
        <div class="stat-label">Ready to build today</div>
      </div>
    </div>

    <h2>Pattern clusters</h2>
    <div class="grid">
      <div class="card">
        <h3>Auth methods (dominance)</h3>
        {auth_bars}
      </div>
      <div class="card">
        <h3>Buildability verdict</h3>
        {build_bars}
      </div>
      <div class="card">
        <h3>Easy wins (self-serve + ready)</h3>
        <ul class="compact">
          {"".join(f"<li>{esc(n)}</li>" for n in patterns['easy_wins']) or "<li>Run full pipeline</li>"}
        </ul>
      </div>
      <div class="card">
        <h3>Needs outreach (gated / blocked)</h3>
        <ul class="compact">
          {"".join(f"<li>{esc(n)}</li>" for n in patterns['outreach']) or "<li>Run full pipeline</li>"}
        </ul>
      </div>
    </div>

    <h2>Agent workflow</h2>
    <div class="card">
      <div class="flow">
apps.json (100 apps)
  → research_agent.py
      → Tavily (discover docs URLs)
      → Firecrawl (scrape top doc pages)
      → Groq (structured extraction; heuristic fallback when rate-limited)
      → data/results_v1.json + optional Supabase
  → verify_agent.py
      → HTTP / Playwright (re-fetch evidence)
      → Keyword checks (+ optional Groq re-verification)
      → data/results_v2.json + verification.json
  → generate_html.py → case-study/index.html
      </div>
      <p style="margin-top:1rem;color:var(--muted);">
        <strong>Human needed:</strong> final pattern interpretation, 15–20 manual spot-checks,
        apps where docs are ambiguous or partner-gated (correct finding = "gated").
        <strong>Free stack:</strong> Groq, Tavily, Firecrawl, Supabase, GitHub Pages — $0.
      </p>
    </div>

    <h2>Verification (accuracy loop)</h2>
    <p>Pass 1 automated extraction → Pass 2 verifier on sample → human cross-check. Stats from last run:</p>
    <div class="grid">
      <div class="card"><div class="stat">{stats.get('correct', '—')}</div><div class="stat-label">Correct</div></div>
      <div class="card"><div class="stat">{stats.get('partial', '—')}</div><div class="stat-label">Partial</div></div>
      <div class="card"><div class="stat">{stats.get('wrong', '—')}</div><div class="stat-label">Wrong</div></div>
      <div class="card"><div class="stat">{stats.get('unverifiable', '—')}</div><div class="stat-label">Unverifiable</div></div>
    </div>
    <div class="table-wrap" style="margin-top:1rem;">
      <table>
        <thead><tr><th>App</th><th>Verdict</th><th>Evidence</th><th>Note</th></tr></thead>
        <tbody>{render_verification(verifications)}</tbody>
      </table>
    </div>

    <h2>Full research matrix ({len(results)} apps)</h2>
    <input type="text" id="search" placeholder="Filter by name, category, auth...">
    <div class="table-wrap">
      <table id="matrix">
        <thead>
          <tr>
            <th>#</th><th>App</th><th>Category</th><th>One-liner</th>
            <th>Auth</th><th>Self-serve</th><th>API</th><th>Build</th>
            <th>Blocker</th><th>MCP</th><th>Conf</th><th>Evidence</th>
          </tr>
        </thead>
        <tbody>{render_table(results)}</tbody>
      </table>
    </div>

    <h2>Honest limitations</h2>
    <div class="card">
      <ul>
        <li>Pass 1 used heuristic extraction (keyword rules on Tavily/Firecrawl text) after Groq daily limit — confidence capped at 55%. Re-run with LLM when credits reset for higher accuracy.</li>
        <li>Verification sample uses keyword matching on evidence pages; JS-heavy docs may show "unverifiable" — use <code>--playwright</code> or manual spot-checks.</li>
        <li>Partner-gated APIs (Salesforce, Meta Ads, PitchBook) are valid "blocked" findings, not failures.</li>
        <li>Firecrawl free tier limits deep scraping; Tavily snippets used as fallback.</li>
        <li>MCP detection relies on docs/GitHub mentions; may miss unofficial servers.</li>
      </ul>
    </div>
  </div>
  <script>
    document.getElementById('search').addEventListener('input', function(e) {{
      const q = e.target.value.toLowerCase();
      document.querySelectorAll('#matrix tbody tr').forEach(row => {{
        row.style.display = row.textContent.toLowerCase().includes(q) ? '' : 'none';
      }});
    }});
  </script>
</body>
</html>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="", help="Input JSON path")
    args = parser.parse_args()

    ensure_dirs()
    results = pick_results(args.input or None)
    if not results:
        raise SystemExit("No results found. Run research_agent.py first.")

    patterns = compute_patterns(results)
    meta = load_json(META, default={})
    verifications = load_json(VERIFICATION, default=[])

    out = CASE_STUDY_DIR / "index.html"
    html_content = build_html(results, patterns, meta, verifications)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"Case study written to {out}")
    print(f"Apps: {len(results)} | Ready: {patterns['buildability_counter'].get('ready', 0)}")


if __name__ == "__main__":
    main()
