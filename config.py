"""Shared configuration and utilities for the research pipeline."""

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CASE_STUDY_DIR = ROOT / "case-study"

load_dotenv(ROOT / ".env")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b")
GROQ_MODEL_FALLBACKS = os.getenv(
    "GROQ_MODEL_FALLBACKS",
    "openai/gpt-oss-20b,qwen/qwen3.6-27b",
).split(",")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY", "")

RESULTS_V1 = DATA_DIR / "results_v1.json"
RESULTS_V2 = DATA_DIR / "results_v2.json"
VERIFICATION = DATA_DIR / "verification.json"
META = DATA_DIR / "meta.json"

EXTRACTION_SCHEMA = {
    "one_liner": "What the app does in one sentence",
    "auth_methods": ["OAuth2", "API key", "Basic", "token", "other"],
    "self_serve": "self_serve | trial | paid_plan | admin_approval | partnership | contact_sales | no_api",
    "self_serve_detail": "Brief explanation of how devs get credentials",
    "api_type": "REST | GraphQL | both | SDK_only | CLI | none | unknown",
    "api_breadth": "broad | moderate | narrow | none | unknown",
    "mcp_exists": False,
    "mcp_detail": "URL or note if MCP server exists",
    "buildability": "ready | partial | blocked | no_api",
    "blocker": "Main blocker if not ready, or 'none'",
    "evidence_urls": ["https://..."],
    "confidence": 0.0,
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    CASE_STUDY_DIR.mkdir(exist_ok=True)


def load_apps() -> list[dict[str, Any]]:
    with open(ROOT / "apps.json", encoding="utf-8") as f:
        return json.load(f)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default if default is not None else {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Any) -> None:
    ensure_dirs()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def parse_json_from_llm(text: str) -> dict[str, Any]:
    """Extract JSON object from LLM response, with light repair for common LLM mistakes."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty LLM response")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in response: {text[:200]}")
    blob = text[start : end + 1]
    # Common LLM JSON fixes
    blob = re.sub(r",\s*}", "}", blob)
    blob = re.sub(r",\s*]", "]", blob)
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        # Try to salvage minimal fields with regex
        overall = re.search(r'"overall"\s*:\s*"([^"]+)"', blob)
        note = re.search(r'"verification_note"\s*:\s*"([^"]*)"', blob)
        if overall:
            return {
                "overall": overall.group(1),
                "field_checks": [],
                "verification_note": note.group(1) if note else "",
            }
        raise


def retry(fn, retries: int = 3, delay: float = 2.0):
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            if attempt == retries - 1:
                raise
            print(f"  Retry {attempt + 1}/{retries}: {e}")
            time.sleep(delay * (attempt + 1))


def groq_chat(client, messages: list, max_tokens: int = 1500, temperature: float = 0.1) -> str:
    """Call Groq with model fallback on rate limits."""
    from groq import Groq

    models = [GROQ_MODEL] + [m.strip() for m in GROQ_MODEL_FALLBACKS if m.strip()]
    last_err = None
    for model in models:
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                last_err = e
                err = str(e).lower()
                if "429" in err or "rate" in err:
                    time.sleep(5 * (attempt + 1))
                    continue
                break
    raise last_err or RuntimeError("Groq chat failed")


def sync_supabase(record: dict[str, Any]) -> None:
    """Optional: push result row to Supabase if configured."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return
    try:
        from supabase import create_client

        client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
        row = {
            "app_id": record.get("id"),
            "app_name": record.get("name"),
            "category": record.get("category"),
            "data": record,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        client.table("app_research").upsert(row, on_conflict="app_id").execute()
    except Exception as e:
        print(f"  Supabase sync skipped: {e}")
