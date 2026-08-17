"""Run full pipeline: research → verify → HTML."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(cmd: list[str]) -> int:
    print(f"\n>>> {' '.join(cmd)}\n")
    return subprocess.call(cmd, cwd=ROOT)


def main():
    steps = [
        [sys.executable, "research_agent.py", "--resume"],
        [sys.executable, "verify_agent.py", "--sample", "20"],
        [sys.executable, "generate_html.py"],
    ]
    for step in steps:
        code = run(step)
        if code != 0:
            sys.exit(code)
    print("\nDone. Open case-study/index.html or deploy to GitHub Pages.")


if __name__ == "__main__":
    main()
