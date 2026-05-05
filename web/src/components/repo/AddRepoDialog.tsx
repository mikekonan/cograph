import { apiFetch } from "@/api/client";
import { ApiError, ConflictError, ValidationError } from "@/api/errors";
import type { RepoVisibility, Repository, RepositoryExistsExtra } from "@/api/types";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import { useCreateRepo } from "@/hooks/useRepos";
import { isValidHost, isValidRepoSegment, parseGitUrl } from "@/lib/repoPath";
import { cn } from "@/lib/utils";
import { useQueryClient } from "@tanstack/react-query";
import { Plus, Upload } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { Link } from "react-router";

type Tab = "git" | "zip";

const MAX_ARCHIVE_BYTES = 200 * 1024 * 1024;

function isRepositoryExistsExtra(value: Record<string, unknown>): value is RepositoryExistsExtra {
  return (
    typeof value.host === "string" &&
    typeof value.owner === "string" &&
    typeof value.name === "string" &&
    typeof value.existing_url === "string"
  );
}

export function AddRepoDialog() {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("git");

  function reset() {
    setTab("git");
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        setOpen(v);
        if (!v) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <Plus className="h-4 w-4" />
          Add repo
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add a repository</DialogTitle>
          <DialogDescription>
            Clone a git repo, or upload a .zip archive (single-shot snapshot, no auto-sync).
          </DialogDescription>
        </DialogHeader>

        <div
          role="tablist"
          aria-label="Add repo source"
          className="flex gap-1 border-b border-[color:var(--color-border)]"
        >
          <TabButton active={tab === "git"} onClick={() => setTab("git")}>
            Git URL
          </TabButton>
          <TabButton active={tab === "zip"} onClick={() => setTab("zip")}>
            Upload .zip
          </TabButton>
        </div>

        {tab === "git" ? (
          <GitUrlForm onSuccess={() => setOpen(false)} />
        ) : (
          <ZipUploadForm onSuccess={() => setOpen(false)} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "px-3 py-2 text-sm font-medium -mb-px border-b-2",
        active
          ? "border-[color:var(--color-accent)] text-[color:var(--color-fg-default)]"
          : "border-transparent text-[color:var(--color-fg-muted)] hover:text-[color:var(--color-fg-default)]",
      )}
    >
      {children}
    </button>
  );
}

// --- Git URL tab -----------------------------------------------------------

function GitUrlForm({ onSuccess }: { onSuccess: () => void }) {
  const [gitUrl, setGitUrl] = useState("");
  const [branch, setBranch] = useState("");
  const [didEditBranch, setDidEditBranch] = useState(false);
  const [name, setName] = useState("");
  const [visibility, setVisibility] = useState<RepoVisibility>("admin_only");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [topError, setTopError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<RepositoryExistsExtra | null>(null);
  const [previewBlurred, setPreviewBlurred] = useState(false);

  const createRepo = useCreateRepo();

  const previewSlug = useMemo(() => parseGitUrl(gitUrl), [gitUrl]);
  const previewError =
    previewBlurred && gitUrl.trim() !== "" && previewSlug === null
      ? "Could not parse host/owner/name from this URL"
      : null;

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFieldErrors({});
    setTopError(null);
    setConflict(null);
    try {
      const trimmedBranch = branch.trim();
      const branchPayload = didEditBranch && trimmedBranch !== "" ? trimmedBranch : undefined;
      const trimmedName = name.trim();
      await createRepo.mutateAsync({
        git_url: gitUrl.trim(),
        branch: branchPayload,
        name: trimmedName || undefined,
        visibility,
      });
      onSuccess();
    } catch (err) {
      if (err instanceof ValidationError) {
        const map: Record<string, string> = {};
        for (const fe of err.fieldErrors) map[fe.field] = fe.message;
        setFieldErrors(map);
      } else if (err instanceof ConflictError) {
        if (err.code === "REPOSITORY_EXISTS" && isRepositoryExistsExtra(err.extras)) {
          setConflict(err.extras);
        } else {
          setTopError(err.message);
        }
      } else if (err instanceof ApiError) {
        setTopError(err.message);
      } else {
        setTopError("Unexpected error. Try again.");
      }
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5">
      {topError && (
        <div
          role="alert"
          className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
        >
          {topError}
        </div>
      )}

      {conflict && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
        >
          <span>
            A repository at{" "}
            <code className="font-mono">
              {conflict.host}/{conflict.owner}/{conflict.name}
            </code>{" "}
            already exists.
          </span>
          <Link
            to={conflict.existing_url}
            onClick={onSuccess}
            className="underline underline-offset-2"
          >
            View existing
          </Link>
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="add-repo-git-url"
          className="text-xs font-medium text-[color:var(--color-fg-muted)]"
        >
          Git URL
        </label>
        <Input
          id="add-repo-git-url"
          value={gitUrl}
          onChange={(e) => setGitUrl(e.target.value)}
          onBlur={() => setPreviewBlurred(true)}
          placeholder="https://github.com/owner/repo.git"
          invalid={!!fieldErrors.git_url || !!previewError}
          autoFocus
          required
        />
        {fieldErrors.git_url ? (
          <p className="text-xs text-[color:var(--color-danger)]">{fieldErrors.git_url}</p>
        ) : previewError ? (
          <p className="text-xs text-[color:var(--color-danger)]">{previewError}</p>
        ) : previewSlug ? (
          <p className="text-xs text-[color:var(--color-fg-muted)]">
            Will be created as{" "}
            <code className="font-mono text-[color:var(--color-fg-default)]">
              {previewSlug.host}/{previewSlug.owner}/{previewSlug.name}
            </code>
          </p>
        ) : null}
      </div>

      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="add-repo-name"
          className="text-xs font-medium text-[color:var(--color-fg-muted)]"
        >
          Display name{" "}
          <span className="font-normal text-[color:var(--color-fg-subtle)]">(optional)</span>
        </label>
        <Input
          id="add-repo-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. My API"
        />
      </div>

      <div className="flex flex-col gap-1.5">
        <label
          htmlFor="add-repo-branch"
          className="text-xs font-medium text-[color:var(--color-fg-muted)]"
        >
          Branch <span className="font-normal text-[color:var(--color-fg-subtle)]">(optional)</span>
        </label>
        <Input
          id="add-repo-branch"
          value={branch}
          onChange={(e) => {
            setBranch(e.target.value);
            setDidEditBranch(true);
          }}
          placeholder="auto-detect (usually main)"
          invalid={!!fieldErrors.branch}
        />
        {fieldErrors.branch && (
          <p className="text-xs text-[color:var(--color-danger)]">{fieldErrors.branch}</p>
        )}
      </div>

      <VisibilityField value={visibility} onChange={setVisibility} />

      <DialogFooter>
        <Button
          type="button"
          variant="secondary"
          onClick={onSuccess}
          disabled={createRepo.isPending}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={createRepo.isPending || !gitUrl.trim()}>
          {createRepo.isPending ? "Queuing…" : "Add repo"}
        </Button>
      </DialogFooter>
    </form>
  );
}

// --- Upload .zip tab -------------------------------------------------------

function ZipUploadForm({ onSuccess }: { onSuccess: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [host, setHost] = useState("");
  const [owner, setOwner] = useState("");
  const [name, setName] = useState("");
  const [visibility, setVisibility] = useState<RepoVisibility>("admin_only");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [topError, setTopError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<RepositoryExistsExtra | null>(null);
  const [pending, setPending] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const idempotencyKeyRef = useRef<string | null>(null);
  const qc = useQueryClient();

  const trimmedHost = host.trim();
  const trimmedOwner = owner.trim();
  const trimmedName = name.trim();
  const hostValid = trimmedHost === "" || isValidHost(trimmedHost);
  const ownerValid = trimmedOwner === "" || isValidRepoSegment(trimmedOwner);
  const nameValid = trimmedName === "" || isValidRepoSegment(trimmedName);
  const allFilled =
    file !== null && trimmedHost !== "" && trimmedOwner !== "" && trimmedName !== "";
  const allValid = hostValid && ownerValid && nameValid;

  function pickFile(picked: File | null) {
    setFieldErrors({});
    setTopError(null);
    if (!picked) {
      setFile(null);
      return;
    }
    if (!picked.name.toLowerCase().endsWith(".zip")) {
      setFieldErrors({ archive: "Filename must end with .zip" });
      setFile(null);
      return;
    }
    if (picked.size > MAX_ARCHIVE_BYTES) {
      setFieldErrors({
        archive: `Archive is ${(picked.size / 1024 / 1024).toFixed(1)} MB; max ${MAX_ARCHIVE_BYTES / 1024 / 1024} MB`,
      });
      setFile(null);
      return;
    }
    setFile(picked);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFieldErrors({});
    setTopError(null);
    setConflict(null);
    if (!file) {
      setFieldErrors({ archive: "Choose a .zip archive" });
      return;
    }

    const form = new FormData();
    form.set("archive", file, file.name);
    form.set("host", trimmedHost);
    form.set("owner", trimmedOwner);
    form.set("name", trimmedName);
    form.set("visibility", visibility);

    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current = crypto.randomUUID();
    }

    setPending(true);
    try {
      const res = await apiFetch("/api/repos/upload", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKeyRef.current },
        body: form,
      });
      const created = (await res.json()) as Repository;
      qc.setQueryData(["repo", created.host, created.owner, created.name], created);
      qc.invalidateQueries({ queryKey: ["repos"] });
      idempotencyKeyRef.current = null;
      onSuccess();
    } catch (err) {
      if (err instanceof ValidationError) {
        const map: Record<string, string> = {};
        for (const fe of err.fieldErrors) map[fe.field] = fe.message;
        setFieldErrors(map);
      } else if (err instanceof ConflictError) {
        if (err.code === "REPOSITORY_EXISTS" && isRepositoryExistsExtra(err.extras)) {
          setConflict(err.extras);
        } else {
          setTopError(err.message);
        }
      } else if (err instanceof ApiError) {
        setTopError(err.message);
      } else {
        setTopError("Upload failed. Try again.");
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5">
      {topError && (
        <div
          role="alert"
          className="rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
        >
          {topError}
        </div>
      )}

      {conflict && (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 rounded-[var(--radius)] border border-[color:var(--color-danger)]/50 bg-[color:var(--color-danger)]/10 px-3 py-2 text-sm text-[color:var(--color-danger)]"
        >
          <span>
            A repository at{" "}
            <code className="font-mono">
              {conflict.host}/{conflict.owner}/{conflict.name}
            </code>{" "}
            already exists.
          </span>
          <Link
            to={conflict.existing_url}
            onClick={onSuccess}
            className="underline underline-offset-2"
          >
            View existing
          </Link>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="add-repo-zip-host"
            className="text-xs font-medium text-[color:var(--color-fg-muted)]"
          >
            Host
          </label>
          <Input
            id="add-repo-zip-host"
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="e.g. local.zip"
            invalid={!hostValid || !!fieldErrors.host}
            required
          />
          {fieldErrors.host ? (
            <p className="text-xs text-[color:var(--color-danger)]">{fieldErrors.host}</p>
          ) : !hostValid ? (
            <p className="text-xs text-[color:var(--color-danger)]">
              DNS-style segment (lowercase, max 253 chars)
            </p>
          ) : null}
        </div>
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="add-repo-zip-owner"
            className="text-xs font-medium text-[color:var(--color-fg-muted)]"
          >
            Owner
          </label>
          <Input
            id="add-repo-zip-owner"
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            placeholder="e.g. demo"
            invalid={!ownerValid || !!fieldErrors.owner}
            required
          />
          {fieldErrors.owner ? (
            <p className="text-xs text-[color:var(--color-danger)]">{fieldErrors.owner}</p>
          ) : !ownerValid ? (
            <p className="text-xs text-[color:var(--color-danger)]">{"[A-Za-z0-9._-]{1,100}"}</p>
          ) : null}
        </div>
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="add-repo-zip-name"
            className="text-xs font-medium text-[color:var(--color-fg-muted)]"
          >
            Name
          </label>
          <Input
            id="add-repo-zip-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. my-api"
            invalid={!nameValid || !!fieldErrors.name}
            required
          />
          {fieldErrors.name ? (
            <p className="text-xs text-[color:var(--color-danger)]">{fieldErrors.name}</p>
          ) : !nameValid ? (
            <p className="text-xs text-[color:var(--color-danger)]">{"[A-Za-z0-9._-]{1,100}"}</p>
          ) : null}
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">Archive</span>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragActive(true);
          }}
          onDragLeave={() => setDragActive(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragActive(false);
            const dropped = e.dataTransfer.files?.[0] ?? null;
            pickFile(dropped);
          }}
          className={cn(
            "flex flex-col items-center justify-center gap-2 rounded-[var(--radius)] border-2 border-dashed px-4 py-6 text-sm transition-colors",
            dragActive
              ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent)]/5"
              : fieldErrors.archive
                ? "border-[color:var(--color-danger)]/60"
                : "border-[color:var(--color-border)] hover:border-[color:var(--color-fg-muted)]",
          )}
        >
          <Upload className="h-5 w-5 text-[color:var(--color-fg-muted)]" />
          {file ? (
            <>
              <span className="font-medium text-[color:var(--color-fg-default)]">{file.name}</span>
              <span className="text-xs text-[color:var(--color-fg-muted)]">
                {(file.size / 1024 / 1024).toFixed(2)} MB · click to change
              </span>
            </>
          ) : (
            <>
              <span className="text-[color:var(--color-fg-default)]">
                Drop a .zip here, or click to browse
              </span>
              <span className="text-xs text-[color:var(--color-fg-muted)]">
                Max 200 MB compressed
              </span>
            </>
          )}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".zip,application/zip"
          aria-label="Archive"
          className="hidden"
          onChange={(e) => pickFile(e.target.files?.[0] ?? null)}
        />
        {fieldErrors.archive && (
          <p className="text-xs text-[color:var(--color-danger)]">{fieldErrors.archive}</p>
        )}
      </div>

      <VisibilityField value={visibility} onChange={setVisibility} />

      <p className="text-xs text-[color:var(--color-fg-muted)]">
        Uploaded archives are extracted server-side. Auto-sync, webhooks, and reindex are disabled —
        re-upload to refresh.
      </p>

      <DialogFooter>
        <Button type="button" variant="secondary" onClick={onSuccess} disabled={pending}>
          Cancel
        </Button>
        <Button type="submit" disabled={pending || !allFilled || !allValid}>
          {pending ? "Uploading…" : "Upload archive"}
        </Button>
      </DialogFooter>
    </form>
  );
}

function VisibilityField({
  value,
  onChange,
}: {
  value: RepoVisibility;
  onChange: (v: RepoVisibility) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">Visibility</span>
      <Select value={value} onValueChange={(v) => onChange(v as RepoVisibility)}>
        <SelectTrigger aria-label="Visibility">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="public">Public</SelectItem>
          <SelectItem value="admin_only">Private</SelectItem>
        </SelectContent>
      </Select>
      <p className="text-xs text-[color:var(--color-fg-muted)]">
        Public repos show up for non-admin browsing when global public read is enabled. Private
        repos are visible to admins only.
      </p>
    </div>
  );
}
