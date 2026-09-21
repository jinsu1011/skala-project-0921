"""Render Mermaid source to PNG locally (vendored mermaid.js + headless Chrome; no network, no upload)."""
from __future__ import annotations

import html
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MERMAID_JS = ROOT / "report" / "vendor" / "mermaid.min.js"
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome", "chromium", "chromium-browser", "microsoft-edge",
]


def find_chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if Path(c).exists() or shutil.which(c):
            return shutil.which(c) or c
    return None


def render(mmd: str, out_png: Path, width: int = 1500, height: int = 4200) -> Path:
    chrome = find_chrome()
    if chrome is None:
        raise RuntimeError("Chrome/Edge not found for Mermaid rendering")
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<style>body{{margin:0;background:#fff;font-family:'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif}}
#g{{display:inline-block;padding:16px}}</style>
<script>{MERMAID_JS.read_text()}</script></head><body><div id="g"><pre class="mermaid">{html.escape(mmd)}</pre></div>
<script>mermaid.initialize({{startOnLoad:true,theme:'default',flowchart:{{htmlLabels:true,curve:'basis',useMaxWidth:false,nodeSpacing:28,rankSpacing:34}},
themeVariables:{{fontFamily:"'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif",fontSize:'15px'}}}});</script>
</body></html>"""
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "g.html"
        f.write_text(page, encoding="utf-8")
        shot = Path(td) / "shot.png"
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                        f"--window-size={width},{height}", "--virtual-time-budget=8000",
                        f"--screenshot={shot}", f.as_uri()], check=True, capture_output=True, timeout=120)
        _trim(shot, out_png)
    return out_png


def _trim(src: Path, dst: Path, pad: int = 24) -> None:
    from PIL import Image, ImageChops

    im = Image.open(src).convert("RGB")
    bbox = ImageChops.difference(im, Image.new("RGB", im.size, (255, 255, 255))).getbbox()
    if bbox:
        l, t, r, b = bbox
        im = im.crop((max(0, l - pad), max(0, t - pad), min(im.width, r + pad), min(im.height, b + pad)))
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst)


if __name__ == "__main__":
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    print(render(src.read_text(encoding="utf-8"), dst))
