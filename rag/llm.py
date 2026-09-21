"""Shared LLM factory with a persistent response cache (committed for key-less replay)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
LLM_CACHE = ROOT / "data" / "llm_cache.sqlite"

load_dotenv(ROOT / ".env")


@lru_cache(maxsize=1)
def _cfg() -> dict:
    return yaml.safe_load((ROOT / "config.yaml").read_text())


@lru_cache(maxsize=1)
def _enable_cache() -> None:
    from langchain_community.cache import SQLiteCache
    from langchain_core.globals import set_llm_cache

    LLM_CACHE.parent.mkdir(parents=True, exist_ok=True)
    set_llm_cache(SQLiteCache(database_path=str(LLM_CACHE)))


def has_openai_key() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def get_llm(role: str = "generator"):
    """role: generator | judge. Temperature 0 + fixed seed for determinism."""
    from langchain_openai import ChatOpenAI

    _enable_cache()
    c = _cfg()["llm"]
    return ChatOpenAI(
        model=c[role],
        temperature=c["temperature"],
        seed=c["seed"],
        max_retries=4,
        timeout=120,
        # a placeholder key lets cached responses replay without a real key (cache hit = no network call)
        api_key=os.getenv("OPENAI_API_KEY") or "sk-offline-replay",
    )
