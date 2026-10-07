"""Observability outside the State (Notion C: 관측성 위치 / 상관).

- decision(): one JSON line per control decision {trace_id, run_id, node, decision, reason, ...} appended to
  outputs/logs/decisions-<run_id>.jsonl and the Python logger. The State only keeps the ids needed to join the two.
- span(): a LangSmith child span with correlation metadata (run_id, task_id, perspective, assigned_agent,
  reviewer_agent, attempt). No-op when tracing is off or langsmith is missing.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os

from graph.runtime import ROOT, now

LOG_DIR = ROOT / "outputs" / "logs"
log = logging.getLogger("orchestration")


def decision(state: dict, node: str, decision: str, reason: str = "", **extra) -> None:
    rec = {"ts": now(), "trace_id": state.get("trace_id", ""), "run_id": state.get("run_id", ""), "node": node,
           "decision": decision, "reason": reason, **extra}
    log.info("decision %s", json.dumps(rec, ensure_ascii=False, default=str))
    if os.getenv("ORCH_DECISION_LOG", "1") == "0" or not rec["run_id"]:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / f"decisions-{rec['run_id']}.jsonl", "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


@contextlib.contextmanager
def span(name: str, **metadata):
    if os.getenv("LANGSMITH_TRACING", "").lower() != "true":
        yield
        return
    try:
        import langsmith
    except ImportError:
        yield
        return
    with langsmith.trace(name=name, run_type="chain", metadata={k: v for k, v in metadata.items() if v is not None},
                         tags=[f"{k}:{v}" for k, v in metadata.items() if k in ("task_id", "assigned_agent",
                                                                                   "reviewer_agent") and v]):
        yield
