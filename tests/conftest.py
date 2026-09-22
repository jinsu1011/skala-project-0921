"""Shared fixtures. No test calls a real API: LLM, web and retriever are replaced by fakes."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from graph.state import Evidence, Technology  # noqa: E402


@pytest.fixture
def techs():
    return [Technology(tech_id="turboquant", name="TurboQuant", camp="SW", developer="Google", developer_groups=["google"]),
            Technology(tech_id="itme", name="ITME", camp="HW", developer="SK hynix", developer_groups=["skhynix"])]


def web(i: str, group: str, tech="turboquant", stance="pro", title="", scope="tech_specific", cls="third_party"):
    return Evidence(evidence_id=f"W:{i}", kind="web", claim=f"claim {i}", title=title or f"title {i}", tech=tech,
                    source_url=f"https://{group}/{i}", source_group=group, origin_group=group, stances=[stance],
                    scope=scope, source_class=cls, publisher=group)


@pytest.fixture
def mkweb():
    return web
