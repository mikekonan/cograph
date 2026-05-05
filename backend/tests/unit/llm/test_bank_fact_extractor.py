from __future__ import annotations

from sqlalchemy import select

from backend.app.llm.bank_fact_extractor import BankFactExtractorService
from backend.app.llm.completion import FakeCompletionProvider
from backend.app.llm.embedder import FakeEmbedProvider
from backend.app.models.bank import Bank, BankDocument, BankDocumentChunk, BankEntity, BankFact, BankObservation
from backend.app.models.user import User


async def test_extract_documents_writes_facts_entities_and_observations(db_session):
    owner = User(email="owner@example.com", password_hash="hashed")
    bank = Bank(name="Runbooks", owner=owner)
    document = BankDocument(
        bank=bank,
        title="Ops guide",
        source_key="runbooks/repo.md",
        content="# Ops\n\nRetry when the repo is not ready.\n",
        content_hash="doc-hash",
        bytes=40,
        document_metadata={},
    )
    chunk = BankDocumentChunk(
        document=document,
        chunk_index=0,
        heading_path=["Ops"],
        content="Retry when the repo is not ready.",
        content_hash="chunk-hash",
    )
    db_session.add_all([owner, bank, document, chunk])
    await db_session.commit()

    extractor = BankFactExtractorService(
        llm=FakeCompletionProvider(
            response=(
                "```json\n"
                "{\n"
                '  "facts": [\n'
                '    {\n'
                '      "statement": "Retry repository actions after indexing finishes.",\n'
                '      "observation": "The runbook says to retry when the repo is not ready.",\n'
                '      "entities": [\n'
                '        {"name": "repository indexing", "type": "system", "role": "subject"}\n'
                "      ]\n"
                "    }\n"
                "  ]\n"
                "}\n"
                "```"
            )
        ),
        embed_provider=FakeEmbedProvider(dims=8),
    )

    result = await extractor.extract_documents(
        session=db_session,
        document_ids=[document.id],
    )

    facts = list((await db_session.scalars(select(BankFact))).all())
    entities = list((await db_session.scalars(select(BankEntity))).all())
    observations = list((await db_session.scalars(select(BankObservation))).all())

    assert result.extracted_facts == 1
    assert result.extracted_entities == 1
    assert result.extracted_observations == 1
    assert facts[0].statement == "Retry repository actions after indexing finishes."
    assert facts[0].heading_path == ["Ops"]
    assert facts[0].embedding is not None
    assert facts[0].model == "fake-embed-v1"
    assert entities[0].canonical_name == "repository indexing"
    assert observations[0].role == "subject"
