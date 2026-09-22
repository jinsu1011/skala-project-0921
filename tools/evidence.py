"""Evidence ids and source grouping (C.2): registered domain -> source_group, vendor families, origin_group."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from graph.runtime import config
from graph.state import Evidence

# one company = one family even across several sites (C.2: research.google and blog.google are one family)
VENDOR_FAMILIES: dict[str, list[str]] = {
    "google": ["google.com", "research.google", "blog.google", "googleblog.com", "deepmind.google", "deepmind.com",
               "withgoogle.com", "googleapis.com"],
    "skhynix": ["skhynix.com", "skhynix.co.kr"],
    "sktelecom": ["sktelecom.com", "sk.com"],
    "nvidia": ["nvidia.com"],
    "samsung": ["samsung.com", "samsungsemiconductor.com"],
    "micron": ["micron.com"],
    "intel": ["intel.com"],
    "amd": ["amd.com"],
    "microsoft": ["microsoft.com", "azure.com"],
    "amazon": ["amazon.com", "aboutamazon.com", "amazon.science"],
    "meta": ["meta.com", "fb.com"],
    "deepseek": ["deepseek.com"],
    "marvell": ["marvell.com"],
    "astera": ["asteralabs.com"],
    "montage": ["montage-tech.com"],
    "huawei": ["huawei.com"],
}
ACADEMIC = ["arxiv.org", "openreview.net", "acm.org", "usenix.org", "ieee.org", "neurips.cc", "mlr.press",
            "aclanthology.org", "semanticscholar.org", "researchgate.net"]
_SECOND_LEVEL = {"co", "com", "ac", "or", "go", "ne", "re", "org", "net", "gov", "edu"}


def registered_domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def vendor_family(domain: str) -> str | None:
    for fam, doms in VENDOR_FAMILIES.items():
        if any(domain == d or domain.endswith("." + d) for d in doms):
            return fam
    return None


def classify_url(url: str) -> tuple[str, str]:
    """-> (source_group, source_class)."""
    dom = registered_domain(url)
    fam = vendor_family(dom)
    if fam:
        return fam, "vendor"
    if any(dom == a or dom.endswith("." + a) for a in ACADEMIC):
        return dom, "academic"
    return dom, "third_party"


def web_id(url: str) -> str:
    norm = re.sub(r"[#?].*$", "", url.strip()).rstrip("/")
    return "W:" + hashlib.sha1(norm.encode()).hexdigest()[:10]


def paper_id(chunk_id: str) -> str:
    return f"P:{chunk_id}"


def developer_groups(tech_id: str) -> list[str]:
    return list(config().get("tech_meta", {}).get(tech_id, {}).get("developer_groups", []))


def paper_origin(tech: str, role: str, doc_id: str) -> tuple[str, str, str]:
    """A selected technology's own paper is the developer's statement (vendor); other papers are academic."""
    devs = developer_groups(tech)
    if role == "primary" and devs:
        return f"arxiv:{doc_id}", "vendor", devs[0]
    return f"arxiv:{doc_id}", "academic", f"paper:{doc_id}"


def normalize_title(t: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", " ", t.lower()).strip()[:80]


def assign_origin_groups(items: list[Evidence], republished: dict[str, str] | None = None) -> list[Evidence]:
    """origin_group = source_group, except (a) items flagged as re-reporting a vendor release take the vendor family,
    (b) items with the same normalised title across domains share one family (syndicated copies)."""
    republished = republished or {}
    title_owner: dict[str, str] = {}
    out = []
    for e in sorted(items, key=lambda x: x.evidence_id):
        og = e.origin_group or e.source_group
        if e.kind == "web":
            if e.evidence_id in republished and republished[e.evidence_id]:
                og = republished[e.evidence_id]
            nt = normalize_title(e.title)
            if nt and len(nt) > 20:
                og = title_owner.setdefault(nt, og)
        out.append(e.model_copy(update={"origin_group": og}))
    return out


def origins(evs: list[Evidence], exclude: list[str] | None = None) -> set[str]:
    ex = set(exclude or [])
    return {e.origin_group for e in evs if e.origin_group not in ex}


def max_origin_share(evs: list[Evidence]) -> float:
    web = [e for e in evs if e.kind == "web"]
    if not web:
        return 0.0
    counts: dict[str, int] = {}
    for e in web:
        counts[e.origin_group] = counts.get(e.origin_group, 0) + 1
    return max(counts.values()) / len(web)


def cap_origin_share(evs: list[Evidence], limit: float = 0.5) -> list[Evidence]:
    """Keep the web evidence set within the 50% single-family rule by dropping the dominant family's surplus
    (lowest-ranked first; the input order is the rank order). Papers are not part of the web share."""
    web = [e for e in evs if e.kind == "web"]
    others = [e for e in evs if e.kind != "web"]
    while web:
        counts: dict[str, int] = {}
        for e in web:
            counts[e.origin_group] = counts.get(e.origin_group, 0) + 1
        top, n = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
        if n / len(web) <= limit or len(counts) == 1:  # one family alone cannot satisfy the rule; Judge flags it
            break
        idx = max(i for i, e in enumerate(web) if e.origin_group == top)
        web.pop(idx)
    return others + web
