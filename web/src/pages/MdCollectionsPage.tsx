import type { MdCollectionVisibility } from "@/api/mdCollections";
import { DocsTabs } from "@/components/md/DocsTabs";
import { MdCollectionGrid } from "@/components/md/MdCollectionGrid";
import { EmptyState } from "@/components/shared/EmptyState";
import { Skeleton } from "@/components/shared/Skeleton";
import { Button } from "@/components/ui/Button";
import {
  Dialog,
  DialogClose,
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
import { useCreateMdCollection, useMdCollections } from "@/hooks/useMdCollections";
import { FolderOpen, Plus } from "lucide-react";
import { useEffect, useState } from "react";

export default function MdCollectionsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const { data, isLoading } = useMdCollections(page, 20, debouncedSearch || undefined);
  const create = useCreateMdCollection();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [visibility, setVisibility] = useState<MdCollectionVisibility>("private");

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional page reset on search change
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  function handleCreate() {
    if (!name.trim()) return;
    create.mutate(
      { name, description: desc || undefined, visibility },
      {
        onSuccess: () => {
          setName("");
          setDesc("");
          setVisibility("private");
          setDialogOpen(false);
        },
      },
    );
  }

  return (
    <main className="mx-auto flex w-full max-w-[90rem] flex-col gap-6 px-5 py-8">
      <header className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-2">
          <h1 className="text-2xl font-semibold tracking-tight md:text-3xl">Collections</h1>
          <p className="text-sm text-[color:var(--color-fg-muted)]">
            Organize markdown documents into collections for RAG.
          </p>
        </div>
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="h-4 w-4" />
              Add
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create collection</DialogTitle>
              <DialogDescription>Collections group markdown documents for RAG.</DialogDescription>
            </DialogHeader>
            <div className="flex flex-col gap-3 py-2">
              <Input placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />
              <Input
                placeholder="Description (optional)"
                value={desc}
                onChange={(e) => setDesc(e.target.value)}
              />
              <div className="flex flex-col gap-1.5">
                <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">
                  Visibility
                </span>
                <Select
                  value={visibility}
                  onValueChange={(value) => setVisibility(value as MdCollectionVisibility)}
                >
                  <SelectTrigger aria-label="Visibility">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="private">Private</SelectItem>
                    <SelectItem value="public">Public</SelectItem>
                    <SelectItem value="admin_only">Admin-only</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="secondary">Cancel</Button>
              </DialogClose>
              <Button onClick={handleCreate} disabled={create.isPending || !name.trim()}>
                {create.isPending ? "Creating…" : "Create"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </header>

      <DocsTabs className="mb-2" />

      <div className="mb-4">
        <Input
          placeholder="Search collections…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {isLoading && <CollectionsSkeleton />}

      {data && data.items.length === 0 && !search && (
        <EmptyState
          icon={FolderOpen}
          title="No collections yet"
          description="Create a collection to start organizing markdown documents for RAG."
          action={
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
              <DialogTrigger asChild>
                <Button>Create your first</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Create collection</DialogTitle>
                  <DialogDescription>
                    Collections group markdown documents for RAG.
                  </DialogDescription>
                </DialogHeader>
                <div className="flex flex-col gap-3 py-2">
                  <Input
                    placeholder="Name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                  />
                  <Input
                    placeholder="Description (optional)"
                    value={desc}
                    onChange={(e) => setDesc(e.target.value)}
                  />
                  <div className="flex flex-col gap-1.5">
                    <span className="text-xs font-medium text-[color:var(--color-fg-muted)]">
                      Visibility
                    </span>
                    <Select
                      value={visibility}
                      onValueChange={(value) => setVisibility(value as MdCollectionVisibility)}
                    >
                      <SelectTrigger aria-label="Visibility">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="private">Private</SelectItem>
                        <SelectItem value="public">Public</SelectItem>
                        <SelectItem value="admin_only">Admin-only</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>
                <DialogFooter>
                  <DialogClose asChild>
                    <Button variant="secondary">Cancel</Button>
                  </DialogClose>
                  <Button onClick={handleCreate} disabled={create.isPending || !name.trim()}>
                    {create.isPending ? "Creating…" : "Create"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          }
        />
      )}

      {data && data.items.length === 0 && search && (
        <EmptyState
          variant="compact"
          title="No matches"
          description={`No collections match "${search}".`}
        />
      )}

      {data && data.items.length > 0 && (
        <>
          <MdCollectionGrid collections={data.items} />

          {data.total_pages > 1 && (
            <div className="mt-6 flex items-center gap-4">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
              >
                Previous
              </Button>
              <span className="text-sm text-[color:var(--color-fg-muted)]">
                Page {page} of {data.total_pages}
              </span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setPage((p) => Math.min(data.total_pages, p + 1))}
                disabled={page >= data.total_pages}
              >
                Next
              </Button>
            </div>
          )}
        </>
      )}
    </main>
  );
}

function CollectionsSkeleton() {
  return (
    <div className="grid gap-4 grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="flex flex-col gap-3 rounded-[var(--radius-md)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-4"
        >
          <Skeleton className="h-5 w-3/4" />
          <Skeleton className="h-3 w-1/2" />
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-3 w-2/3" />
        </div>
      ))}
    </div>
  );
}
