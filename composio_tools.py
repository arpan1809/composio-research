"""
Optional: discover Composio toolkits related to research (Firecrawl, Tavily, etc.).
Run after setting COMPOSIO_API_KEY — shows how Composio fits the assignment stack.

Usage: python composio_tools.py
"""

import os
from dotenv import load_dotenv

load_dotenv()

COMPOSIO_API_KEY = os.getenv("COMPOSIO_API_KEY", "")
if not COMPOSIO_API_KEY:
    raise SystemExit("Set COMPOSIO_API_KEY in .env")

try:
    from composio import Composio

    client = Composio(api_key=COMPOSIO_API_KEY)
    # Search for research-related toolkits
    keywords = ["firecrawl", "tavily", "browser", "search", "scrape"]
    print("Composio toolkits matching research workflow:\n")
    for kw in keywords:
        try:
            tools = client.tools.get(toolkits=[kw], limit=3)
            if tools:
                print(f"  [{kw}] {len(tools)} tools available")
                for t in tools[:2]:
                    name = getattr(t, "name", None) or t.get("name", "tool")
                    print(f"    - {name}")
        except Exception:
            pass
    print("\nThis pipeline uses Tavily + Firecrawl APIs directly for free-tier control.")
    print("Swap to composio.tools.execute() for Composio-managed auth and MCP gateway.")
except ImportError:
    print("Install composio: pip install composio")
