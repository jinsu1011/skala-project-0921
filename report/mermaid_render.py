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


# Subgraph titles are centred by Mermaid, so edges entering a cluster run through them.
# After layout, move each title to the first of centre/left/right that clears edge lines,
# edge labels and nodes, then lift it above the edges with a halo in the cluster fill.
TIDY_JS = r"""
function tidyTitles() {
  const svg = document.querySelector('#g svg'); if (!svg) return;
  const top = svg.querySelector(':scope > g');
  const R = el => el.getBoundingClientRect();
  const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  const pts = [];
  svg.querySelectorAll('.edgePaths path, path.flowchart-link').forEach(p => {
    const m = p.getScreenCTM(), n = p.getTotalLength();
    for (let d = 0; d <= n; d += 3) pts.push(p.getPointAtLength(d).matrixTransform(m));
  });
  const nodes = [...svg.querySelectorAll('.node')].map(R);
  const onLine = r => pts.some(q => q.x > r.left && q.x < r.right && q.y > r.top && q.y < r.bottom);
  const onNode = r => nodes.some(b => hit(r, b));
  const titles = [];
  // A node outside a cluster can straddle its top edge (dagre bounds clusters by rank);
  // push that edge below the node and move the title with it.
  svg.querySelectorAll('g.cluster').forEach(c => {
    const rect = c.querySelector(':scope > rect'), lab = c.querySelector(':scope > .cluster-label');
    const cr = R(rect);
    const lo = Math.max(0, ...nodes.filter(n => n.top < cr.top && n.bottom > cr.top && n.left < cr.right && n.right > cr.left)
                                   .map(n => n.bottom + 8 - cr.top));
    if (!lo) return;
    rect.setAttribute('y', +rect.getAttribute('y') + lo);
    rect.setAttribute('height', +rect.getAttribute('height') - lo);
    const down = lab ? Math.max(0, cr.top + lo + 4 - R(lab).top) : 0;
    if (down) lab.setAttribute('transform', (lab.getAttribute('transform') || '') + ` translate(0,${down})`);
  });
  svg.querySelectorAll('g.cluster').forEach(c => {
    const rect = c.querySelector(':scope > rect'), lab = c.querySelector(':scope > .cluster-label');
    if (!rect || !lab) return;
    const cr = R(rect), lr = R(lab), pad = 6;
    const grow = (r, dx) => ({left: r.left + dx - pad, right: r.right + dx + pad, top: r.top - 2, bottom: r.bottom + 2});
    const cands = [0, cr.left + 12 - lr.left, cr.right - 12 - lr.right];
    const dx = cands.find(d => !onLine(grow(lr, d)) && !onNode(grow(lr, d)))
            ?? cands.find(d => !onNode(grow(lr, d))) ?? 0;
    const m = top.getScreenCTM().inverse().multiply(lab.getScreenCTM()).translate(dx, 0);
    const fo = lab.querySelector('foreignObject'), w = +fo.getAttribute('width'), h = +fo.getAttribute('height');
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('transform', `matrix(${m.a},${m.b},${m.c},${m.d},${m.e},${m.f})`);
    const f = getComputedStyle(rect).fill;
    fo.querySelectorAll('span, div, p').forEach(t => t.style.textShadow =
      [[-2,0],[2,0],[0,-2],[0,2],[-1.5,-1.5],[1.5,-1.5],[-1.5,1.5],[1.5,1.5]].map(([x, y]) => `${x}px ${y}px 0 ${f}`).join(','));
    g.appendChild(fo); lab.remove(); top.appendChild(g);
    titles.push(R(g));
  });
  // Edge labels go on top; a label that sits on a node, a title or another label
  // slides along its own edge to the nearest free spot.
  const paths = [...svg.querySelectorAll('.edgePaths path, path.flowchart-link')].map(p => {
    const m = p.getScreenCTM(), n = p.getTotalLength(), a = [];
    for (let d = 0; d <= n; d += 2) a.push(p.getPointAtLength(d).matrixTransform(m));
    return a;
  });
  const placed = [];
  svg.querySelectorAll('g.edgeLabels').forEach(g => g.parentNode.appendChild(g));
  svg.querySelectorAll('g.edgeLabel').forEach(el => {
    const r = R(el); if (r.width < 2) return;
    const cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
    const busy = (x, y) => { const b = {left: x - r.width / 2 - 3, right: x + r.width / 2 + 3,
                                        top: y - r.height / 2 - 3, bottom: y + r.height / 2 + 3};
      return nodes.concat(titles, placed).some(o => hit(b, o)); };
    let best = null;
    if (busy(cx, cy)) {
      const path = paths.reduce((acc, a) => {
        const d = Math.min(...a.map(q => Math.hypot(q.x - cx, q.y - cy)));
        return d < acc.d ? {a, d} : acc; }, {a: [], d: Infinity}).a;
      best = path.filter(q => !busy(q.x, q.y))
                 .sort((u, v) => Math.hypot(u.x - cx, u.y - cy) - Math.hypot(v.x - cx, v.y - cy))[0] || null;
    }
    if (best) el.setAttribute('transform', (el.getAttribute('transform') || '') + ` translate(${best.x - cx},${best.y - cy})`);
    placed.push(R(el));
  });
}
"""


def render(mmd: str, out_png: Path, width: int = 1500, height: int = 4200, font_px: int = 15,
           rank_spacing: int = 34, padding: int = 15, node_spacing: int = 24, title_margin: int = 0) -> Path:
    chrome = find_chrome()
    if chrome is None:
        raise RuntimeError("Chrome/Edge not found for Mermaid rendering")
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<style>body{{margin:0;background:#fff;font-family:'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif}}
#g{{display:inline-block;padding:16px}}</style>
<script>{MERMAID_JS.read_text()}</script></head><body><div id="g"><pre class="mermaid">{html.escape(mmd)}</pre></div>
<script>mermaid.initialize({{startOnLoad:false,theme:'default',flowchart:{{htmlLabels:true,curve:'basis',useMaxWidth:false,nodeSpacing:{node_spacing},rankSpacing:{rank_spacing},padding:{padding},subGraphTitleMargin:{{top:{title_margin},bottom:{title_margin}}}}},
themeVariables:{{fontFamily:"'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif",fontSize:'{font_px}px'}}}});
mermaid.run().then(tidyTitles);
{TIDY_JS}</script>
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
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1500
    fp = int(sys.argv[4]) if len(sys.argv) > 4 else 15
    rs = int(sys.argv[5]) if len(sys.argv) > 5 else 34
    pad = int(sys.argv[6]) if len(sys.argv) > 6 else 15
    ns = int(sys.argv[7]) if len(sys.argv) > 7 else 24
    tm = int(sys.argv[8]) if len(sys.argv) > 8 else 0
    print(render(src.read_text(encoding="utf-8"), dst, width=w, font_px=fp, rank_spacing=rs, padding=pad,
                 node_spacing=ns, title_margin=tm))
