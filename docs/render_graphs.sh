#!/usr/bin/env bash
# Re-render design figures locally (vendored mermaid.js + headless Chrome). Args: width font_px rank_spacing
set -euo pipefail
cd "$(dirname "$0")/.."
uv run python report/mermaid_render.py docs/graph_overview.mmd docs/graph_overview.png 1500 15 22
uv run python report/mermaid_render.py docs/graph_quality.mmd docs/graph_quality.png 1800 15 16
