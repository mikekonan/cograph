from backend.app.models.enums import SyncStep

# Single source of truth for pipeline step order and user-facing titles.
REPO_SYNC_STEPS: tuple[tuple[SyncStep, str], ...] = (
    (SyncStep.CLONE, "Clone repository"),
    (SyncStep.PARSE, "Parse AST"),
    (SyncStep.EXTRACT_GRAPH, "Extract call graph"),
    (SyncStep.EMBED, "Embed code nodes"),
    (SyncStep.INDEX_REPO_DOCS, "Index repo documents"),
    (SyncStep.EMBED_REPO_DOCS, "Embed repo document chunks"),
    (SyncStep.GENERATE_SUMMARIES, "Generate code summaries"),
    (SyncStep.GENERATE_WIKI, "Generate wiki documents"),
)
