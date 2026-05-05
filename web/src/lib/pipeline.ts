import type { SyncStep } from "@/api/types";

/**
 * Canonical execution order of sync pipeline steps. Small enough that a
 * constant beats a runtime lookup; shared by the MSW ticker (so queued
 * jobs are promoted in pipeline order), the JobsPage BatchCard (so steps
 * inside a batch card render clone → parse → … even if the wire response
 * arrived in arbitrary order), and the stats aggregator. Exporting a map
 * rather than an array makes "which step runs first" an O(1) comparison
 * in `.sort()` callbacks.
 */
export const PIPELINE_ORDER: Record<SyncStep, number> = {
  clone: 0,
  parse: 1,
  extract_graph: 2,
  embed: 3,
  index_repo_docs: 4,
  embed_repo_docs: 5,
  generate_summaries: 6,
  generate_wiki: 7,
  // Export / import run after a successful repo sync, so they sit at the
  // end. They never coexist with the six repo-sync steps in a single
  // batch, so sharing an index doesn't cause ordering ambiguity.
  export_confluence: 8,
  import_bank: 8,
};
