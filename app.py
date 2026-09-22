"""KV cache multi-perspective evaluation Agentic RAG - entry point.

    uv run python app.py [--offline] [--tech sw=turboquant,hw=itme] [--no-rerank] [--rebuild-index]

Without API keys the run switches to offline replay automatically (committed LLM and web caches only).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def parse_tech(s: str | None) -> dict[str, str] | None:
    if not s:
        return None
    out = {}
    for part in s.split(","):
        k, _, v = part.partition("=")
        if k.strip() not in ("sw", "hw") or not v.strip():
            raise SystemExit(f"--tech 형식 오류: '{part}'. 예) --tech sw=turboquant,hw=itme")
        out[k.strip()] = v.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="KV cache 다관점 평가 Agentic RAG")
    ap.add_argument("--offline", action="store_true", help="저장된 LLM·웹 캐시만 사용(API 호출 없음)")
    ap.add_argument("--tech", help="선정 기술 교체, 예: sw=turboquant,hw=itme")
    ap.add_argument("--no-rerank", action="store_true", help="reranker 생략")
    ap.add_argument("--rebuild-index", action="store_true", help="청크·FAISS 인덱스 다시 만들기")
    args = ap.parse_args(argv)

    from graph.runtime import OfflineCacheMiss, config, configure, has_keys

    openai_key, tavily_key = has_keys()
    offline = args.offline or not (openai_key and tavily_key)
    if offline:
        os.environ["LANGSMITH_TRACING"] = "false"   # no network in replay mode
        os.environ.pop("LANGCHAIN_TRACING_V2", None)
    techs = parse_tech(args.tech) or dict(config()["selected_techs"])
    unknown = [v for v in techs.values() if v not in config()["tech_meta"]]
    if unknown:
        raise SystemExit(f"알 수 없는 기술: {unknown}. 사용 가능: {list(config()['tech_meta'])}")
    rt = configure(offline=offline, rerank=not args.no_rerank, rebuild_index=args.rebuild_index, techs=techs)

    log_dir = ROOT / "outputs" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(log_dir / f"run-{stamp}.log"), logging.StreamHandler()])
    log = logging.getLogger("app")
    mode = "offline 재생" + ("(API 키 없음, 자동 전환)" if not args.offline and offline else "") if offline else "online"
    log.info("mode=%s techs=%s rerank=%s rebuild_index=%s", mode, techs, not args.no_rerank, args.rebuild_index)

    from graph.workflow import RECURSION_LIMIT, build_graph

    graph = build_graph()
    t0 = time.time()
    final = {}
    try:
        for chunk in graph.stream({}, config={"recursion_limit": RECURSION_LIMIT, "run_name": "kvcache-eval"},
                                  stream_mode=["updates", "values"]):
            kind, data = chunk
            if kind == "updates":
                for node in data:
                    log.info("node done: %s", node)
            else:
                final = data
    except OfflineCacheMiss as ex:
        log.error("%s", ex)
        return 2
    elapsed = time.time() - t0
    summary = {
        "mode": mode, "elapsed_s": round(elapsed, 1), "pdf": final.get("report_pdf_path"),
        "llm_calls": rt.llm_calls, "llm_cache_hits": rt.llm_cache_hits, "web_calls": rt.web_calls,
        "web_cache_hits": rt.web_cache_hits, "usage": rt.usage,
        "perspective_retry_count": final.get("perspective_retry_count"),
        "judge": {k: v.passed for k, v in (final.get("judge_scores") or {}).items()},
        "warnings": final.get("warnings", []),
    }
    (log_dir / f"run-{stamp}.json").write_text(json.dumps(
        summary | {"audit_log": [e.model_dump() for e in final.get("audit_log", [])]}, ensure_ascii=False, indent=1))
    snap = {k: (v.model_dump() if hasattr(v, "model_dump") else
                {kk: vv.model_dump() for kk, vv in v.items()} if isinstance(v, dict) and v and hasattr(next(iter(v.values())), "model_dump")
                else v)
            for k, v in final.items() if k in ("trl_result", "market_result", "stakeholder_result", "domain_result",
                                               "synthesis", "judge_scores", "perspective_retry_count", "warnings")}
    snap["evidence"] = [e.model_dump() for e in final.get("evidence", [])]
    (ROOT / "outputs" / "state_snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1, default=str))
    log.info("done: %s", json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
