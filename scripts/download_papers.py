"""Download missing corpus PDFs from arXiv and verify the 200-page cap."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pymupdf
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
PAPER_DIR = ROOT / "data" / "papers"


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for p in cfg["corpus"]["papers"]:
        path = PAPER_DIR / f"{p['arxiv']}.pdf"
        if not path.exists():
            url = f"https://arxiv.org/pdf/{p['arxiv']}"
            print(f"downloading {url}")
            r = requests.get(url, timeout=60, headers={"User-Agent": "skala-kvcache-eval/0.1"})
            r.raise_for_status()
            path.write_bytes(r.content)
            time.sleep(3)  # be polite to arXiv
        pages = pymupdf.open(path).page_count
        total += pages
        print(f"{p['arxiv']:>11}  {p['tech']:<10} {pages:>3} pages")
    cap = cfg["corpus"]["page_cap"]
    print(f"total pages: {total} (cap {cap})")
    return 0 if total <= cap else 1


if __name__ == "__main__":
    sys.exit(main())
