"""Agent Registry: the worker pool the Orchestrator can assign SubTasks to.

The four specialists wrap the existing perspective agents unchanged (TRL / market / stakeholder / domain). The three
research agents cover open perspectives the Orchestrator may add (regulation, competition/ecosystem, cost, deployment,
...). Agents the initial plan does not use become independent cross-reviewers when the Judge fails a task.
Agent 0 (feedback_retry_coordinator) is not in the pool: it never executes or reviews a task.
"""
from __future__ import annotations

from graph.state import AgentProfile

MINIMUM_PERSPECTIVES = ("trl", "market", "stakeholder", "domain")   # coverage requirement, not a fan-out list
MINIMUM_KO = {"trl": "기술 성숙도(TRL)", "market": "시장성", "stakeholder": "이해관계자", "domain": "도메인 적용성"}

REGISTRY: dict[str, AgentProfile] = {a.agent_id: a for a in [
    AgentProfile(agent_id="trl_specialist", name="TRL specialist", worker_type="trl",
                 capabilities=["technology readiness", "TRL ladder", "evidence validation", "release status"]),
    AgentProfile(agent_id="market_specialist", name="Market specialist", worker_type="market",
                 capabilities=["market knowledge", "adoption", "commercialization", "cost structure"]),
    AgentProfile(agent_id="stakeholder_specialist", name="Stakeholder specialist", worker_type="stakeholder",
                 capabilities=["stakeholder analysis", "industry reaction", "source bias", "developer statements"]),
    AgentProfile(agent_id="domain_specialist", name="Domain specialist", worker_type="domain",
                 capabilities=["domain applicability", "long-context serving", "workload fit", "deployment constraints"]),
    AgentProfile(agent_id="regulation_specialist", name="Regulation specialist", worker_type="research",
                 capabilities=["regulation", "standards", "compliance", "export control", "evidence validation"]),
    AgentProfile(agent_id="ecosystem_specialist", name="Competition/Ecosystem specialist", worker_type="research",
                 capabilities=["competition", "ecosystem", "alternatives", "market knowledge", "partnerships"]),
    AgentProfile(agent_id="research_generalist", name="Generic Research specialist", worker_type="research",
                 capabilities=["general research", "evidence validation", "cost structure", "implementation risk",
                               "operational considerations"]),
]}

FEEDBACK_COORDINATOR = "feedback_retry_coordinator"   # Agent 0


def worker_type(agent_id: str) -> str:
    return REGISTRY[agent_id].worker_type


def used_unused(subtasks) -> tuple[list[str], list[str]]:
    """Deterministic: used = assigned agents of the initial SubTasks, unused = registry - used (both sorted)."""
    used = sorted({t.assigned_agent for t in subtasks})
    return used, sorted(set(REGISTRY) - set(used))


def registry_table() -> list[dict]:
    return [a.model_dump() for a in REGISTRY.values()]
