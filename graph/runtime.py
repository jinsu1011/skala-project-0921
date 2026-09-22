"""Run-time services shared by nodes: config, offline switch, cached LLM calls, retriever handle, audit helpers.

Every LLM response is stored in data/cache/llm_agents.sqlite keyed by (model, messages). The cache is committed, so
`--offline` (or a missing API key) replays the same run without network access. A cache miss in offline mode raises
OfflineCacheMiss with a remedy instead of calling the API.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache"
LLM_CACHE_DB = CACHE_DIR / "llm_agents.sqlite"

load_dotenv(ROOT / ".env")


class OfflineCacheMiss(RuntimeError):
    pass


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text())


@dataclass
class Runtime:
    offline: bool = False
    rerank: bool = True
    rebuild_index: bool = False
    techs: dict[str, str] = field(default_factory=lambda: dict(config()["selected_techs"]))
    llm_calls: int = 0
    llm_cache_hits: int = 0
    web_calls: int = 0
    web_cache_hits: int = 0
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    _retriever: object = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---------------------------------------------------------- retriever
    def retriever(self):
        if self._retriever is None:
            with self._lock:
                if self._retriever is None:
                    from rag.embedder import RUNTIME_MAX_SEQ
                    from rag.retriever import HybridRetriever

                    r = config()["retrieval"]
                    self._retriever = HybridRetriever(
                        r["embedding_model"], rrf_k=r["rrf_k"], reranker=r["reranker"] if self.rerank else None,
                        rebuild=self.rebuild_index, max_len=RUNTIME_MAX_SEQ)
        return self._retriever


_RT = Runtime()


def rt() -> Runtime:
    return _RT


def configure(**kw) -> Runtime:
    global _RT
    _RT = Runtime(**kw)
    return _RT


def has_keys() -> tuple[bool, bool]:
    return bool(os.getenv("OPENAI_API_KEY")), bool(os.getenv("TAVILY_API_KEY"))


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def audit(node: str, **detail):
    from graph.state import AuditEvent

    return [AuditEvent(node=node, at=now(), detail=detail)]


# ---------------------------------------------------------------- LLM with a committed response cache
_db_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(LLM_CACHE_DB, timeout=30, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS llm (key TEXT PRIMARY KEY, model TEXT, tag TEXT, response TEXT)")
    return con


def _key(model: str, system: str, user: str, json_mode: bool) -> str:
    raw = json.dumps([model, system, user, json_mode, config()["llm"]["temperature"], config()["llm"]["seed"]],
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def llm_text(role: str, system: str, user: str, *, tag: str = "", json_mode: bool = False) -> str:
    """role: generator | judge. Deterministic settings (temperature 0, seed 42); cached by exact prompt."""
    model = config()["llm"][role]
    k = _key(model, system, user, json_mode)
    with _db_lock:
        con = _db()
        row = con.execute("SELECT response FROM llm WHERE key=?", (k,)).fetchone()
        con.close()
    r = rt()
    if row:
        r.llm_cache_hits += 1
        return row[0]
    if r.offline:
        raise OfflineCacheMiss(
            f"LLM 캐시에 없는 요청입니다(tag={tag}, model={model}). offline 모드에서는 저장된 응답만 쓸 수 있습니다. "
            "해결: OPENAI_API_KEY를 .env에 넣고 --offline 없이 한 번 실행해 캐시를 만들거나, 저장소의 data/cache/ 를 최신으로 받으세요.")
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    c = config()["llm"]
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    llm = ChatOpenAI(model=model, temperature=c["temperature"], seed=c["seed"], max_retries=4, timeout=180,
                     model_kwargs=kwargs)
    msg = llm.invoke([SystemMessage(system), HumanMessage(user)], config={"run_name": tag or role})
    text = msg.content if isinstance(msg.content, str) else str(msg.content)
    um = getattr(msg, "usage_metadata", None) or {}
    with _db_lock:
        r.llm_calls += 1
        r.usage["input_tokens"] += um.get("input_tokens", 0)
        r.usage["output_tokens"] += um.get("output_tokens", 0)
        con = _db()
        con.execute("INSERT OR REPLACE INTO llm VALUES (?,?,?,?)", (k, model, tag, text))
        con.commit()
        con.close()
    return text


def llm_json(role: str, system: str, user: str, *, tag: str = "") -> dict:
    text = llm_text(role, system, user, tag=tag, json_mode=True)
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0) if m else text, strict=False)
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------- comparative-wording lexicon (C.2, C.8)
BANNED_TERMS = ["우수하다", "우수한", "우수함", "열등", "더 낫", "더 좋", "뛰어나", "압도", "우위", "승자", "패자", "승리",
                "추천한다", "추천함", "권장한다", "권고한다", "최고의", "최선의", "가장 좋은", "앞선다", "뒤처진다", "능가",
                "superior", "inferior", "outperform", "recommend", "winner", "better than", "best choice"]
_REPLACE = {"우수하다": "높게 보고됐다", "우수한": "높게 보고된", "우수함": "높게 보고됨", "뛰어나": "높게 보고되",
            "더 낫": "다르", "더 좋": "다르", "우위": "차이", "압도": "큰 차이", "추천한다": "조건을 정리한다",
            "추천함": "조건을 정리함", "권장한다": "조건을 정리한다", "권고한다": "조건을 정리한다", "최고의": "보고된",
            "최선의": "하나의", "가장 좋은": "보고된", "앞선다": "다르다", "뒤처진다": "다르다", "능가": "상회 보고",
            "열등": "낮게 보고", "승자": "대상", "패자": "대상", "승리": "결과"}
# terms that are legitimate inside design vocabulary and must not be flagged
_ALLOW = ["추천 아님", "추천이 아니", "추천·우열", "우열·추천", "추천을 쓰지", "추천하지 않", "우열이나 추천", "추천이나 우열",
          "우열 판정", "우열·승패", "승패·추천", "추천 금지", "우열 표현", "추천 표현", "우열 어휘", "추천 어휘"]


def lexicon_hits(text: str) -> list[str]:
    t = text
    for a in _ALLOW:
        t = t.replace(a, "")
    low = t.lower()
    return [w for w in BANNED_TERMS if w.lower() in low]


def neutralize(text: str) -> str:
    out = text
    for w, rep in _REPLACE.items():
        out = out.replace(w, rep)
    for w in ("superior", "inferior", "outperform", "recommend", "winner", "better than", "best choice"):
        out = re.sub(w, "reported", out, flags=re.I)
    return out
