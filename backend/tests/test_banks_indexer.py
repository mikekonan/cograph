from __future__ import annotations

from sqlalchemy import select

from backend.app.banks.indexer import BankDocumentUpsertInput, BankIndexer
from backend.app.models.bank import Bank, BankDocument, BankDocumentChunk
from backend.app.models.user import User


async def test_bank_indexer_upserts_documents_by_source_key(db_session):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
    )
    bank = Bank(
        name="Platform ADRs",
        description="Architecture records",
        owner=owner,
    )
    db_session.add(bank)
    await db_session.flush()

    indexer = BankIndexer()
    first_result = await indexer.upsert_document(
        session=db_session,
        bank_id=bank.id,
        document=BankDocumentUpsertInput(
            source_key="adr/ADR-001.md",
            content="# ADR-001\n\nInitial decision.\n",
        ),
    )
    await db_session.commit()

    second_result = await indexer.upsert_document(
        session=db_session,
        bank_id=bank.id,
        document=BankDocumentUpsertInput(
            source_key="adr/ADR-001.md",
            content="# ADR-001\n\nUpdated decision with more context.\n",
        ),
    )
    await db_session.commit()

    documents = list(
        (
            await db_session.scalars(
                select(BankDocument).where(BankDocument.bank_id == bank.id)
            )
        ).all()
    )
    chunks = list(
        (
            await db_session.scalars(
                select(BankDocumentChunk).where(BankDocumentChunk.document_id == documents[0].id)
            )
        ).all()
    )

    assert first_result.created is True
    assert first_result.replaced is False
    assert second_result.created is False
    assert second_result.replaced is True
    assert len(documents) == 1
    assert documents[0].source_key == "adr/ADR-001.md"
    assert documents[0].content == "# ADR-001\n\nUpdated decision with more context.\n"
    assert len(chunks) == second_result.chunk_count


async def test_bank_indexer_batch_counts_unchanged_documents(db_session):
    owner = User(
        email="owner@example.com",
        password_hash="hashed",
    )
    bank = Bank(
        name="Onboarding",
        description=None,
        owner=owner,
    )
    db_session.add(bank)
    await db_session.flush()

    indexer = BankIndexer()
    await indexer.upsert_document(
        session=db_session,
        bank_id=bank.id,
        document=BankDocumentUpsertInput(
            source_key="guides/intro.md",
            content="# Intro\n\nStable content.\n",
        ),
    )
    await db_session.commit()

    result = await indexer.upsert_documents(
        session=db_session,
        bank_id=bank.id,
        documents=[
            BankDocumentUpsertInput(
                source_key="guides/intro.md",
                content="# Intro\n\nStable content.\n",
            ),
            BankDocumentUpsertInput(
                source_key="guides/advanced.md",
                content="# Advanced\n\nFresh content.\n",
            ),
        ],
    )
    await db_session.commit()

    assert result.indexed_documents == 2
    assert result.unchanged_documents == 1
