from __future__ import annotations

from backend.app.rag.agent_context import AgentKnowledgeContext


def test_agent_knowledge_context_defaults():
    ctx = AgentKnowledgeContext()
    assert ctx.query == ""
    assert ctx.intent == ""
    assert ctx.wiki_sections == []
    assert ctx.architecture_sections == []
    assert ctx.commands == []
    assert ctx.interfaces == []
    assert ctx.workflows == []
    assert ctx.code_refs == []
    assert ctx.graph_nodes == []
    assert ctx.graph_edges == []
    assert ctx.repo_docs == []
    assert ctx.risks == []
    assert ctx.suggested_next_actions == []
    assert ctx.missing_evidence == []
    assert ctx.confidence == {}
    assert ctx.staleness == {}


def test_agent_knowledge_context_round_trip():
    ctx = AgentKnowledgeContext(
        query="how do I run tests?",
        repository_id="repo-123",
        source_commit="abc123",
        intent="runbook",
        answer="Use `pytest`.",
        wiki_sections=[{"slug": "testing", "title": "Testing"}],
        commands=[{"name": "pytest", "kind": "command"}],
        confidence={"overall": 0.9},
    )
    dumped = ctx.model_dump(mode="json")
    restored = AgentKnowledgeContext.model_validate(dumped)
    assert restored.query == "how do I run tests?"
    assert restored.commands[0]["name"] == "pytest"
    assert restored.confidence["overall"] == 0.9
