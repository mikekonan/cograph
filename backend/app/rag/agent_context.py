from __future__ import annotations

from pydantic import BaseModel, Field


class AgentKnowledgeContext(BaseModel):
    """Shared agent knowledge context returned by both REST and MCP surfaces.

    Designed so agents do not need to scrape markdown for structured facts;
    every field is machine-readable and includes source refs where applicable.
    """

    query: str = ""
    repository_id: str = ""
    source_commit: str = ""
    intent: str = ""  # runbook | architecture | change-impact | debug | search
    answer: str = ""

    wiki_sections: list[dict[str, object]] = Field(default_factory=list)
    architecture_sections: list[dict[str, object]] = Field(default_factory=list)

    commands: list[dict[str, object]] = Field(default_factory=list)
    interfaces: list[dict[str, object]] = Field(default_factory=list)
    workflows: list[dict[str, object]] = Field(default_factory=list)

    code_refs: list[dict[str, object]] = Field(default_factory=list)
    graph_nodes: list[dict[str, object]] = Field(default_factory=list)
    graph_edges: list[dict[str, object]] = Field(default_factory=list)
    repo_docs: list[dict[str, object]] = Field(default_factory=list)

    risks: list[dict[str, object]] = Field(default_factory=list)
    suggested_next_actions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)

    confidence: dict[str, object] = Field(default_factory=dict)
    staleness: dict[str, object] = Field(default_factory=dict)


__all__ = ["AgentKnowledgeContext"]
