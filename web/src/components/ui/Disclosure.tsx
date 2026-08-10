import { cn } from "@/lib/utils";
import * as CollapsiblePrimitive from "@radix-ui/react-collapsible";
import { ChevronRight } from "lucide-react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { forwardRef } from "react";

/**
 * Disclosure — collapsible section with a chevron trigger.
 * Used for "Relevant source files", collapsed citations, advanced filters.
 * Backed by Radix Collapsible (a11y + animation state classes).
 */

export const Disclosure = CollapsiblePrimitive.Root;

export const DisclosureTrigger = forwardRef<
  HTMLButtonElement,
  ComponentPropsWithoutRef<typeof CollapsiblePrimitive.Trigger> & {
    /** Optional trailing metadata (counter, timestamp) rendered right-aligned. */
    meta?: ReactNode;
  }
>(({ className, children, meta, ...rest }, ref) => (
  <CollapsiblePrimitive.Trigger
    ref={ref}
    className={cn(
      "group flex w-full items-center gap-2 rounded-[var(--radius)]",
      "border border-[color:var(--color-border)] bg-[color:var(--color-bg-surface)]",
      "px-3.5 py-2.5 text-left text-sm font-medium",
      "text-[color:var(--color-fg)] hover:bg-[color:var(--color-bg-hover)]",
      "transition-colors duration-[var(--motion-quick)]",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
      className,
    )}
    {...rest}
  >
    <ChevronRight
      aria-hidden="true"
      className="h-4 w-4 text-[color:var(--color-fg-muted)] transition-transform duration-[var(--motion-base)] ease-[var(--ease-smooth)] group-data-[state=open]:rotate-90"
    />
    <span className="flex-1">{children}</span>
    {meta && <span className="text-xs text-[color:var(--color-fg-muted)]">{meta}</span>}
  </CollapsiblePrimitive.Trigger>
));
DisclosureTrigger.displayName = "DisclosureTrigger";

export const DisclosureContent = forwardRef<
  HTMLDivElement,
  ComponentPropsWithoutRef<typeof CollapsiblePrimitive.Content>
>(({ className, children, ...rest }, ref) => (
  <CollapsiblePrimitive.Content
    ref={ref}
    className={cn(
      "overflow-hidden",
      "data-[state=open]:animate-[disclosure-down_var(--motion-base)_var(--ease-smooth)]",
      "data-[state=closed]:animate-[disclosure-up_var(--motion-quick)_var(--ease-smooth)]",
      className,
    )}
    {...rest}
  >
    <div className="pt-2">{children}</div>
  </CollapsiblePrimitive.Content>
));
DisclosureContent.displayName = "DisclosureContent";
