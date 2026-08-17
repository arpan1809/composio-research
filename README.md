# Composio 100-App Research Agent

Agent pipeline for the Composio AI Product Ops take-home: researches 100 SaaS apps for auth, API surface, self-serve vs gated access, and buildability — then generates a self-contained HTML case study.

**Stack (all free tier):** Groq (LLM), Tavily (search), Firecrawl (scrape), Supabase (optional storage), Playwright (verification), GitHub Pages (deploy).

## Quick start

```bash
# 1. Install
pip install -r requirements.txt
playwright install chromium

# 2. Configure keys (copy from .env.example)
# GROQ_API_KEY, TAVILY_API_KEY, FIRECRAWL_API_KEY, COMPOSIO_API_KEY
# Optional: SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

# 3. Smoke test (2 apps)
python research_agent.py --limit 2

# 4. Full research (100 apps, ~45–90 min)
python research_agent.py --resume

# 5. Verify sample (20 apps)
python verify_agent.py --sample 20

# 6. Optional: Playwright for JS-heavy docs
python verify_agent.py --sample 20 --playwright

# 7. Generate HTML case study
python generate_html.py

# 8. Open locally
# case-study/index.html
```

## Deploy to GitHub Pages (free)

```bash
git init
git add .
git commit -m "Composio 100-app research case study"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/composio-research.git
git push -u origin main
```

In GitHub repo Settings → Pages → Source: `main` branch, folder `/case-study`.

Live URL: `https://YOUR_USERNAME.github.io/composio-research/`

## Project structure

```
apps.json              # 100 apps from assignment
research_agent.py      # Tavily → Firecrawl → Groq pipeline
verify_agent.py        # Evidence re-fetch + accuracy loop
generate_html.py       # JSON → case-study/index.html
config.py              # Shared config and Supabase sync
data/
  results_v1.json      # Pass 1 research
  results_v2.json      # After verification
  verification.json    # Per-app verification verdicts
  meta.json            # Run stats and accuracy
case-study/
  index.html           # Deployable deliverable
```

## Supabase (optional)

Create table `app_research`:

```sql
create table app_research (
  app_id int primary key,
  app_name text,
  category text,
  data jsonb,
  updated_at timestamptz default now()
);
```

## CLI flags

| Script | Flag | Description |
|--------|------|-------------|
| `research_agent.py` | `--limit N` | Research only N apps |
| `research_agent.py` | `--resume` | Skip apps already in results_v1 |
| `research_agent.py` | `--ids 1,2,3` | Research specific IDs |
| `verify_agent.py` | `--sample N` | Verify N random apps |
| `verify_agent.py` | `--low-confidence` | Only verify confidence < 0.75 |
| `verify_agent.py` | `--playwright` | Use browser for evidence pages |
| `generate_html.py` | `--input path` | Use custom results JSON |

## What the agent does vs human

| Step | Automated | Human |
|------|-----------|-------|
| Find docs URLs | Tavily | — |
| Scrape pages | Firecrawl | — |
| Extract fields | Groq | — |
| Re-verify evidence | verify_agent | — |
| Pattern interpretation | HTML generator | Review headlines |
| Accuracy audit | Stats from verifier | 15–20 manual spot-checks |
| Gated/partner APIs | Agent reports "blocked" | Confirm with docs |

## Test it

### Automated checks

```powershell
cd C:\Users\localadmin\Downloads\composio
python test_pipeline.py --quick    # structure + files (no API calls)
python test_pipeline.py            # includes Tavily + Groq connectivity
```

### Step-by-step manual test

```powershell
# 1) Smoke test — 2 apps (~30 sec with LLM, or instant with heuristics)
python research_agent.py --limit 2 --heuristic-only

# 2) Open the case study in your browser
start case-study\index.html

# 3) Regenerate HTML after any research run
python generate_html.py --input data/results_v1.json

# 4) Optional verification sample (needs Groq credits)
python verify_agent.py --sample 10
python generate_html.py
```

### What to look for in the case study

- **Headline patterns** at the top (auth, self-serve, buildability)
- **100-row filterable table** — use the search box
- **Evidence links** open real docs URLs
- **Verification section** after running `verify_agent.py`

### Deploy (free)

Push to GitHub and enable Pages on the `/case-study` folder — see Deploy section above.
