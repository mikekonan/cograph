import { AlertCircle, CheckCircle2, Plug, Save } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError } from "@/api/errors";
import { Skeleton } from "@/components/shared/Skeleton";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { useMcpBriefing, useUpdateMcpBriefing } from "@/hooks/useMcpBriefing";
import { formatRelativeTime } from "@/lib/utils";

/**
 * AdminMcpPage — `/admin?tab=mcp`.
 *
 * Edit the singleton operator briefing. It is added to the instructions
 * every MCP client gets when it connects, under `## This deployment`.
 * Clients show about 2000 chars of instructions, so the briefing gets what
 * the general playbook leaves (`max_length` from the API); a longer briefing
 * is not sent. Backend: `backend/app/api/mcp_admin.py`; the playbook and the
 * limit: `backend/app/mcp/instructions.py`.
 *
 * The textarea has no `maxLength`: a briefing saved under an older, larger
 * limit must show in full, so the operator can see it is over and shorten it.
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

  const remaining = (briefingQuery.data?.max_length ?? 0) - draft.length;
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
          Free-form markdown added to the instructions every MCP client gets when it connects, under{" "}
          <code>## This deployment</code>. Use it to tell agents what this deployment is for, define
          domain vocabulary, point at canonical sources, or set deployment-specific rules. Clients
          show about 2000 characters of instructions, so the briefing gets what the general playbook
          leaves. A longer briefing is not sent at all. Changes reach agents within a minute, on
          their next connect.
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
              rows={16}
              spellCheck={false}
              className="font-mono text-sm leading-relaxed"
              placeholder={
                "This Cograph deployment serves the shipping team.\n\n" +
                "Glossary:\n" +
                "  - carrier: the company that delivers the customer's parcels.\n" +
                "  - rate card: the customer↔carrier price table used by dispatch.\n\n" +
                "When asked about routing or fallback, search catalog AND " +
                "storefront in parallel; both own pieces of the flow."
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
                {overBudget
                  ? `${(-remaining).toLocaleString()} chars over: agents do not get this text`
                  : `${remaining.toLocaleString()} chars left`}
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
                Saved — agents get it within a minute, on their next connect.
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
        </div>
      )}
    </section>
  );
}
