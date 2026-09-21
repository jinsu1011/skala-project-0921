"""Build the corpus-specific cross-lingual retrieval eval set (Korean question -> English gold chunk).

Stages
  generate : sample chunks evenly per paper and let the judge-tier LLM write one Korean question per chunk
  qc       : automatic QC filter (answerability/specificity/self-containedness check + copy-leak check)
  rewrite  : question-only English rewrite (runtime query_rewriter prompt) used by BM25 / EN dense
Outputs  eval/data/qa_candidates.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import tiktoken
from langchain_core.messages import HumanMessage, SystemMessage

from rag.chunker import load_or_build_chunks
from rag.llm import get_llm

OUT = Path(__file__).resolve().parent / "data"
CAND = OUT / "qa_candidates.jsonl"
PER_PAPER = 10
_ENC = tiktoken.get_encoding("cl100k_base")

GEN_SYS = """You write evaluation questions for a retrieval system.
Given ONE English passage from a research paper, write ONE question in natural KOREAN that
- asks about ONE specific detail stated in this passage that is unlikely to be repeated elsewhere in the paper
  (a reported number, a named component and its role, an experimental setting, a stated condition or limitation),
- is a single question (no "and/또한/그리고 ... 무엇인가요" compound questions), at most ~60 Korean characters,
- sounds like what an analyst evaluating KV-cache optimization techniques would ask,
- does NOT copy English phrases from the passage (technical names like KV cache, CXL, vLLM, LongBench are allowed),
- is self-contained: name the technique/paper topic explicitly (e.g. "TurboQuant에서", "ITME 구조에서"), never "이 논문", "위 표".
Return JSON only: {"question": "...", "answer": "short Korean answer", "evidence": "the exact English sentence(s) from the passage that answer it", "qtype": "mechanism|result|condition|limitation|comparison"}"""

QC_SYS = """You are a strict QC reviewer for a retrieval eval set. Given a Korean question and an English passage, answer in JSON:
{"answerable": true/false,   # the passage alone contains the answer
 "specific": true/false,     # the question targets a specific fact, not something any KV-cache paper would answer
 "self_contained": true/false}  # understandable without seeing the passage"""

# Same prompt the runtime query_rewriter uses; it sees ONLY the question (no passage -> no answer leakage).
REWRITE_SYS = """Rewrite the Korean question into a concise English search query for retrieving passages from English
research papers on LLM KV-cache optimization. Keep technique names and technical terms; translate the information need;
do not add facts, numbers, or terms that are not implied by the question. Return only the query."""


def _json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0), strict=False) if m else {}


def sample_chunks(chunks):
    picked = []
    for tech in dict.fromkeys(c.tech for c in chunks):
        pool = [c for c in chunks if c.tech == tech and len(_ENC.encode(c.text)) >= 200
                and not c.section.lower().startswith(("front matter", "related work"))]
        step = len(pool) / PER_PAPER
        picked += [pool[int(i * step)] for i in range(min(PER_PAPER, len(pool)))]
    return picked


def generate():
    chunks = load_or_build_chunks()
    llm = get_llm("judge")

    def one(c):
        msg = [SystemMessage(GEN_SYS), HumanMessage(f"[paper] {c.title}\n[section] {c.section}\n[passage]\n{c.text}")]
        d = _json(llm.invoke(msg).content)
        return {"qid": f"q-{c.chunk_id}", "gold_chunk": c.chunk_id, "tech": c.tech, "camp": c.camp, **d}

    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, sample_chunks(chunks)))
    OUT.mkdir(exist_ok=True)
    CAND.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    print(f"generated {len(rows)} questions")


def _copy_leak(question: str, passage: str) -> bool:
    """True if the question copies >=5 consecutive English words from the passage."""
    words = re.findall(r"[A-Za-z][A-Za-z\-]+", question)
    p = " ".join(re.findall(r"[A-Za-z][A-Za-z\-]+", passage)).lower()
    return any(" ".join(words[i:i + 5]).lower() in p for i in range(len(words) - 4))


def qc():
    chunks = {c.chunk_id: c for c in load_or_build_chunks()}
    rows = [json.loads(l) for l in CAND.read_text().splitlines()]
    llm = get_llm("judge")

    def one(r):
        c = chunks[r["gold_chunk"]]
        d = _json(llm.invoke([SystemMessage(QC_SYS),
                              HumanMessage(f"[question]\n{r['question']}\n[passage]\n{c.text}")]).content)
        r["qc"] = {k: d.get(k) for k in ("answerable", "specific", "self_contained")}
        r["qc"]["copy_leak"] = _copy_leak(r["question"], c.text)
        r["qc"]["pass"] = bool(d.get("answerable") and d.get("specific") and d.get("self_contained")
                               and not r["qc"]["copy_leak"])
        return r

    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, rows))
    CAND.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    print(f"qc pass {sum(r['qc']['pass'] for r in rows)}/{len(rows)}")


def rewrite():
    rows = [json.loads(l) for l in CAND.read_text().splitlines()]
    llm = get_llm("generator")

    def one(r):
        r["query_en"] = llm.invoke([SystemMessage(REWRITE_SYS), HumanMessage(r["question"])]).content.strip().strip('"')
        return r

    with ThreadPoolExecutor(8) as ex:
        rows = list(ex.map(one, rows))
    CAND.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    print(f"rewrote {len(rows)} queries (question-only, generator model)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["generate", "qc", "rewrite"])
    {"generate": generate, "qc": qc, "rewrite": rewrite}[ap.parse_args().stage]()
