import { Breadcrumb } from "@/components/ui/Breadcrumb";
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
import { Disclosure, DisclosureContent, DisclosureTrigger } from "@/components/ui/Disclosure";
import { Input } from "@/components/ui/Input";
import { Kbd } from "@/components/ui/Kbd";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/Select";
import { Textarea } from "@/components/ui/Textarea";
import { Tooltip } from "@/components/ui/Tooltip";
import { cn } from "@/lib/utils";
import { Copy, HelpCircle, Keyboard, Settings } from "lucide-react";
import { useState } from "react";

/**
 * /design subsection — Radix-backed primitives. Rendered above the existing
 * shared-component gallery inside DesignPage.
 */
export function DesignPrimitives() {
  const [lang, setLang] = useState<string>("");
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <div className="flex flex-col gap-12">
      <Header />

      <Section title="Input" subtitle="Form text input with sizes + invalid state.">
        <div className="grid gap-3 md:grid-cols-2">
          <Input placeholder="Default — medium size" />
          <Input size="sm" placeholder="Small (h-8)" />
          <Input size="lg" placeholder="Large (h-10)" />
          <Input invalid defaultValue="Invalid state" />
          <Input disabled placeholder="Disabled input" />
          <Input type="email" placeholder="user@example.com" />
        </div>
      </Section>

      <Section title="Textarea" subtitle="Multi-line input. Used by doc-note and admin forms.">
        <div className="grid gap-3 md:grid-cols-2">
          <Textarea placeholder="Enter message — Shift+Enter for newline" />
          <Textarea invalid defaultValue="Invalid textarea content" rows={3} />
        </div>
      </Section>

      <Section title="Select" subtitle="Radix-backed dropdown. Keyboard nav, typeahead, portal.">
        <div className="flex flex-wrap items-center gap-3">
          <Select value={lang} onValueChange={setLang}>
            <SelectTrigger className="w-56">
              <SelectValue placeholder="Filter by language" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="python">Python</SelectItem>
              <SelectItem value="typescript">TypeScript</SelectItem>
              <SelectItem value="go">Go</SelectItem>
              <SelectItem value="rust">Rust</SelectItem>
              <SelectItem value="java">Java</SelectItem>
            </SelectContent>
          </Select>

          <Select defaultValue="fast">
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="fast">Fast</SelectItem>
              <SelectItem value="balanced">Balanced</SelectItem>
              <SelectItem value="precise">Precise</SelectItem>
            </SelectContent>
          </Select>

          <Select disabled>
            <SelectTrigger className="w-40">
              <SelectValue placeholder="Disabled" />
            </SelectTrigger>
            <SelectContent />
          </Select>
        </div>
        {lang && (
          <p className="mt-2 text-xs text-[color:var(--color-fg-muted)]">
            Selected: <code className="font-mono">{lang}</code>
          </p>
        )}
      </Section>

      <Section
        title="Dialog"
        subtitle="Radix modal. Focus-trap, Esc-to-close, scroll-lock, a11y out of the box."
      >
        <div className="flex gap-3">
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger asChild>
              <Button>Open example dialog</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Re-index repository?</DialogTitle>
                <DialogDescription>
                  This will re-parse every source file and regenerate the graph. May take several
                  minutes on large repos.
                </DialogDescription>
              </DialogHeader>
              <div className="flex flex-col gap-2">
                <label
                  htmlFor="reindex-reason"
                  className="text-xs font-medium text-[color:var(--color-fg-muted)]"
                >
                  Reason (optional)
                </label>
                <Textarea
                  id="reindex-reason"
                  rows={3}
                  placeholder="Why are you re-indexing? (stored in logs)"
                />
              </div>
              <DialogFooter>
                <Button variant="secondary" onClick={() => setDialogOpen(false)}>
                  Cancel
                </Button>
                <Button onClick={() => setDialogOpen(false)}>Re-index</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          <Dialog>
            <DialogTrigger asChild>
              <Button variant="danger">Delete…</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Delete repository</DialogTitle>
                <DialogDescription>
                  This permanently removes the repo, its code graph, and generated docs. This can't
                  be undone.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="secondary">Cancel</Button>
                <Button variant="danger">Delete forever</Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </Section>

      <Section
        title="Disclosure"
        subtitle="Collapsible section with chevron. Used for 'Relevant source files', filters."
      >
        <div className="flex flex-col gap-2 max-w-2xl">
          <Disclosure defaultOpen>
            <DisclosureTrigger meta="3 files">Relevant source files</DisclosureTrigger>
            <DisclosureContent>
              <ul className="flex flex-col gap-1 pl-8 text-sm text-[color:var(--color-fg-muted)]">
                <li>
                  <code className="font-mono text-[color:var(--color-fg)]">auth/login.py</code>
                  <span className="ml-2 text-xs">15-42</span>
                </li>
                <li>
                  <code className="font-mono text-[color:var(--color-fg)]">auth/tokens.py</code>
                  <span className="ml-2 text-xs">8, 45, 62</span>
                </li>
                <li>
                  <code className="font-mono text-[color:var(--color-fg)]">auth/middleware.py</code>
                  <span className="ml-2 text-xs">23-89</span>
                </li>
              </ul>
            </DisclosureContent>
          </Disclosure>

          <Disclosure>
            <DisclosureTrigger meta="advanced">Advanced filters</DisclosureTrigger>
            <DisclosureContent>
              <div className="grid gap-3 rounded-[var(--radius)] border border-[color:var(--color-border-subtle)] bg-[color:var(--color-bg-surface)] p-3 md:grid-cols-2">
                <Input placeholder="Path contains…" size="sm" />
                <Input placeholder="Author…" size="sm" />
              </div>
            </DisclosureContent>
          </Disclosure>
        </div>
      </Section>

      <Section
        title="Tooltip"
        subtitle="Hover hints on icon buttons and truncated labels. 250ms delay by default."
      >
        <div className="flex items-center gap-3">
          <Tooltip content="Copy to clipboard">
            <Button variant="ghost" size="icon">
              <Copy className="h-4 w-4" />
            </Button>
          </Tooltip>
          <Tooltip content="Settings">
            <Button variant="ghost" size="icon">
              <Settings className="h-4 w-4" />
            </Button>
          </Tooltip>
          <Tooltip
            content={
              <span className="inline-flex items-center gap-1">
                Open command palette <Kbd>⌘K</Kbd>
              </span>
            }
          >
            <Button variant="secondary" size="sm">
              <HelpCircle className="h-4 w-4" />
              Help
            </Button>
          </Tooltip>
          <Tooltip content="Hover me" side="right">
            <span className="cursor-help text-sm text-[color:var(--color-fg-muted)] underline-offset-2 [text-decoration-style:dotted] [text-decoration-line:underline]">
              tooltip on text
            </span>
          </Tooltip>
        </div>
      </Section>

      <Section title="Kbd" subtitle="Keyboard shortcut chips.">
        <div className="flex flex-wrap items-center gap-3 text-sm text-[color:var(--color-fg-muted)]">
          <span className="inline-flex items-center gap-1.5">
            Search <Kbd>⌘</Kbd> + <Kbd>K</Kbd>
          </span>
          <span className="inline-flex items-center gap-1.5">
            Send <Kbd>Enter</Kbd>
          </span>
          <span className="inline-flex items-center gap-1.5">
            Newline <Kbd>Shift</Kbd> + <Kbd>Enter</Kbd>
          </span>
          <span className="inline-flex items-center gap-1.5">
            Close <Kbd>Esc</Kbd>
          </span>
          <span className="inline-flex items-center gap-1.5">
            Next doc <Kbd>]</Kbd> · Prev <Kbd>[</Kbd>
          </span>
        </div>
      </Section>

      <Section
        title="Breadcrumb"
        subtitle="Horizontal path with ChevronRight separators. Last item is non-clickable."
      >
        <div className="flex flex-col gap-3">
          <Breadcrumb
            items={[
              { label: "Repos", to: "/" },
              { label: "fastapi/fastapi", to: "/repos/1" },
              { label: "Docs", to: "/repos/1/docs" },
              { label: "Auth Module" },
            ]}
          />
          <Breadcrumb items={[{ label: "Admin", to: "/admin" }, { label: "Providers" }]} />
        </div>
      </Section>
    </div>
  );
}

function Header() {
  return (
    <div className="flex flex-col gap-2 border-t border-[color:var(--color-border-subtle)] pt-8">
      <p className="text-xs uppercase tracking-wide text-[color:var(--color-fg-muted)]">
        <Keyboard className="inline h-3 w-3 mr-1" /> Primitives
      </p>
      <h2 className="text-2xl font-semibold tracking-tight">UI primitives</h2>
      <p className="max-w-3xl text-sm text-[color:var(--color-fg-muted)]">
        Radix-backed low-level components. All forms, dialogs, dropdowns, and tooltips in the rest
        of the app compose from these. Press <Kbd>Tab</Kbd> to verify keyboard navigation; every
        primitive is focus-ring-visible.
      </p>
    </div>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn("flex flex-col gap-3")}>
      <div className="flex flex-col gap-0.5">
        <h3 className="text-xl font-semibold tracking-tight">{title}</h3>
        {subtitle && <p className="text-sm text-[color:var(--color-fg-muted)]">{subtitle}</p>}
      </div>
      {children}
    </section>
  );
}
