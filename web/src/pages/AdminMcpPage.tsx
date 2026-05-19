import { ApiError } from "@/api/errors";
import { MCP_BRIEFING_MAX_LENGTH } from "@/api/mcpBriefing";
import { Skeleton } from "@/components/shared/Skeleton";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { useMcpBriefing, useUpdateMcpBriefing } from "@/hooks/useMcpBriefing";
import { formatRelativeTime } from "@/lib/utils";
import { AlertCircle, CheckCircle2, Plug, Save } from "lucide-react";
import { useEffect, useState } from "react";

/**
 * AdminMcpPage — `/admin?tab=mcp`.
 *
 * Edit the singleton operator briefing that gets inlined into every MCP
 * client's `initialize` payload (FastMCP `instructions=`) and also
 * exposed as the `cograph://briefing` resource. Backend lives in
 * `backend/app/api/mcp_admin.py`; the playbook that wraps this briefing
 * is in `backend/app/mcp/instructions.py`.
 *
 * The 8K char cap is enforced at three layers — the column DDL, the
 * Pydantic schema, and the textarea `maxLength`. We surface a live char
 * counter so an operator pasting a long doc sees the cliff before they
 * hit Save and get a 422.
 */
export default function AdminMcpPage() {
  const briefingQuery = useMcpBriefing();
  const updateMutation = useUpdateMcpBriefing();

  const [draft, setDraft] = useState<string>("");
  const [dirty, setDirty] = useState(false);
  // The toast banner is intentionally local state, not a third-party
  // toast lib — keeps the page self-contained and matches the rest of
  // /admin which doesn't pull in a notification system.
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (briefingQuery.data && !dirty) {
      setDraft(briefingQuery.data.content);
    }
  }, [briefingQuery.data, dirty]);

  const remaining = MCP_BRIEFING_MAX_LENGTH - draft.length;
  const overBudget = remaining < 0;
  const canSave = dirty && !overBudget && !updateMutation.isPending;

  const errorMessage =
    updateMutation.error instanceof ApiError
      ? updateMutation.error.message
      : updateMutation.error
        ? "Could not save the briefing. Try again."
        : null;

  function handleSave() {
    updateMutation.mutate(draft, {
      onSuccess: () => {
        setDirty(false);
        setSavedAt(Date.now());
      },
    });
  }

  function handleReset() {
    if (!briefingQuery.data) return;
    setDraft(briefingQuery.data.content);
    setDirty(false);
  }

  return (
    <section className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
          <Plug className="h-5 w-5" aria-hidden="true" /> MCP operator briefing
        </h2>
        <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
          Free-form markdown surfaced to every MCP client at <code>initialize</code> and via the{" "}
          <code>cograph://briefing</code> resource. Use it to tell agents what this deployment is
          for, define domain vocabulary, point at canonical sources, or set deployment-specific
          rules. The cite-or-bust playbook is appended automatically — write only what's specific to
          this instance.
        </p>
      </header>

      {briefingQuery.isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : briefingQuery.isError ? (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-[var(--radius)] border border-[color:var(--color-danger)]/30 bg-[color:var(--color-danger)]/5 p-3 text-sm text-[color:var(--color-danger)]"
        >
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>Could not load the current briefing. Refresh the page and try again.</span>
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <label
              htmlFor="mcp-briefing-textarea"
              className="text-sm font-medium text-[color:var(--color-fg)]"
            >
              Briefing content
            </label>
            <Textarea
              id="mcp-briefing-textarea"
              value={draft}
              onChange={(e) => {
                setDraft(e.target.value);
                setDirty(true);
              }}
              maxLength={MCP_BRIEFING_MAX_LENGTH}
              rows={16}
              spellCheck={false}
              className="font-mono text-sm leading-relaxed"
              placeholder={
                "This Cograph deployment serves the payments team.\n\n" +
                "Glossary:\n" +
                "  - acquirer: the bank that routes the merchant's card transactions.\n" +
                "  - terminal: the merchant↔acquirer binding used by runner.\n\n" +
                "When asked about routing or fallback, search ledger AND " +
                "processing-api in parallel; both own pieces of the flow."
              }
            />
            <div className="flex items-center justify-between text-xs">
              <span
                className={
                  overBudget
                    ? "text-[color:var(--color-danger)]"
                    : "text-[color:var(--color-fg-muted)]"
                }
              >
                {draft.length.toLocaleString()} / {MCP_BRIEFING_MAX_LENGTH.toLocaleString()} chars
                {overBudget ? ` (${(-remaining).toLocaleString()} over)` : ""}
              </span>
              <span className="text-[color:var(--color-fg-muted)]">
                {briefingQuery.data.updated_at ? (
                  <>
                    Last updated{" "}
                    <time
                      dateTime={briefingQuery.data.updated_at}
                      title={new Date(briefingQuery.data.updated_at).toLocaleString()}
                    >
                      {formatRelativeTime(briefingQuery.data.updated_at)}
                    </time>
                    {briefingQuery.data.updated_by_email ? (
                      <> by {briefingQuery.data.updated_by_email}</>
                    ) : null}
                  </>
                ) : null}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button onClick={handleSave} disabled={!canSave}>
              <Save className="h-4 w-4" aria-hidden="true" />
              {updateMutation.isPending ? "Saving…" : "Save briefing"}
            </Button>
            <Button variant="ghost" onClick={handleReset} disabled={!dirty}>
              Reset
            </Button>
            {savedAt && !dirty && !errorMessage ? (
              <span className="inline-flex items-center gap-1.5 text-xs text-[color:var(--color-success,var(--color-accent))]">
                <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                Saved — new briefing reaches clients on their next MCP <code>initialize</code>.
              </span>
            ) : null}
            {errorMessage ? (
              <span
                role="alert"
                className="inline-flex items-center gap-1.5 text-xs text-[color:var(--color-danger)]"
              >
                <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" />
                {errorMessage}
              </span>
            ) : null}
          </div>

          <div className="flex flex-col gap-2">
            <h3 className="text-sm font-medium text-[color:var(--color-fg)]">
              What the agent sees
            </h3>
            <p className="text-xs text-[color:var(--color-fg-muted)]">
              Your briefing is dropped into the rendered playbook between{" "}
              <code>## Operator briefing</code> and the cite-or-bust rules. This preview shows the
              exact section the agent reads — the playbook itself (retry ladder, multi-phrasing
              rule, etc.) wraps around this and isn't editable here.
            </p>
            <pre className="overflow-auto rounded-[var(--radius)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-elevated)] p-3 font-mono text-xs leading-relaxed text-[color:var(--color-fg)]">
              {draft.trim() ? draft : "(empty — the agent will see the built-in default briefing)"}
            </pre>
          </div>
        </div>
      )}
    </section>
  );
}
