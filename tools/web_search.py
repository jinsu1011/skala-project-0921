"""web_search tool (B.7): Tavily search with a committed, date-stamped JSON cache (data/web_cache/).

`--offline` (or no TAVILY_API_KEY) reads the cache only. `days` applies to the news topic; for the general topic it is
mapped to Tavily's `time_range`.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Optional

import requests
from langchain_core.tools import tool

from graph.runtime import ROOT, OfflineCacheMiss, now, rt
from graph.state import Evidence
from tools.evidence import classify_url, web_id

WEB_CACHE = ROOT / "data" / "web_cache"
TAVILY_URL = "https://api.tavily.com/search"


def _time_range(days: Optional[int]) -> Optional[str]:
    if not days:
        return None
    for lim, name in ((1, "day"), (7, "week"), (31, "month"), (366, "year")):
        if days <= lim:
            return name
    return None


def _cache_path(query: str, days: Optional[int], topic: str, max_results: int):
    key = hashlib.sha1(json.dumps([query, days, topic, max_results], ensure_ascii=False).encode()).hexdigest()[:16]
    return WEB_CACHE / f"{key}.json"


def tavily(query: str, days: Optional[int] = None, topic: str = "general", max_results: int = 5) -> list[dict]:
    path = _cache_path(query, days, topic, max_results)
    r = rt()
    if path.exists():
        r.web_cache_hits += 1
        return json.loads(path.read_text())["results"]
    if r.offline or not os.getenv("TAVILY_API_KEY"):
        raise OfflineCacheMiss(
            f"웹 검색 캐시에 없는 질의입니다: '{query}'. 해결: TAVILY_API_KEY를 .env에 넣고 --offline 없이 실행하거나, "
            "저장소의 data/web_cache/ 를 최신으로 받으세요.")
    body = {"query": query, "topic": topic, "max_results": max_results, "search_depth": "basic",
            "include_answer": False}
    if topic == "news" and days:
        body["days"] = days
    elif (tr := _time_range(days)):
        body["time_range"] = tr
    resp = requests.post(TAVILY_URL, json=body, timeout=60,
                         headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"})
    resp.raise_for_status()
    results = [{k: x.get(k) for k in ("url", "title", "content", "published_date", "score")}
               for x in resp.json().get("results", [])]
    WEB_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"query": query, "days": days, "topic": topic, "max_results": max_results,
                                "fetched_at": now(), "results": results}, ensure_ascii=False, indent=1))
    r.web_calls += 1
    return results


def search_web(query: str, days: Optional[int] = None, stance: str = "neutral", tech: str = "",
               perspective: str = "", attempt: int = 0, max_results: int = 5) -> list[Evidence]:
    out = []
    for x in tavily(query, days=days, max_results=max_results):
        url = x.get("url") or ""
        if not url:
            continue
        group, cls = classify_url(url)
        out.append(Evidence(
            evidence_id=web_id(url), kind="web", summary=(x.get("content") or "")[:1200], source_url=url,
            title=(x.get("title") or "").strip(), publisher=group, published_at=(x.get("published_date") or "")[:10],
            tech=tech, perspectives=[perspective] if perspective else [], stances=[stance] if stance in
            ("pro", "con", "neutral") else [], source_class=cls, source_group=group, origin_group=group,
            attempt=attempt))
    return out


@tool
def web_search(query: str, days: Optional[int] = None, stance: str = "neutral") -> list[dict]:
    """Tavily web search. `stance` (pro|con|neutral) labels the intent of the query. Results are cached with the fetch
    date; offline runs use the cache only. Each result carries an id 'W:<hash>', URL, publish date, publisher and a
    vendor / third_party / academic class."""
    return [e.model_dump() for e in search_web(query, days, stance)]
