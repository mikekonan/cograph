import { cn } from "@/lib/utils";
import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { type ComponentPropsWithoutRef, forwardRef } from "react";

/**
 * Select — dropdown for filters and model pickers.
 * Backed by Radix for keyboard nav, typeahead, and portal rendering.
 *
 * Usage:
 *   <Select value={v} onValueChange={setV}>
 *     <SelectTrigger><SelectValue placeholder="Choose..." /></SelectTrigger>
 *     <SelectContent>
 *       <SelectItem value="a">A</SelectItem>
 *     </SelectContent>
 *   </Select>
 */

export const Select = SelectPrimitive.Root;
export const SelectGroup = SelectPrimitive.Group;
export const SelectValue = SelectPrimitive.Value;

export const SelectTrigger = forwardRef<
  HTMLButtonElement,
  ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>
>(({ className, children, ...rest }, ref) => (
  <SelectPrimitive.Trigger
    ref={ref}
    className={cn(
      "flex h-9 w-full items-center justify-between gap-2 rounded-[var(--radius)]",
      "border border-[color:var(--color-border)]",
      "bg-[color:var(--color-bg-subtle)]",
      "px-3 text-sm text-[color:var(--color-fg)]",
      "placeholder:text-[color:var(--color-fg-subtle)]",
      "transition-colors duration-[var(--motion-quick)]",
      "hover:bg-[color:var(--color-bg-hover)]",
      "focus-visible:outline-none focus-visible:border-[color:var(--color-accent)]",
      "focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
      "disabled:cursor-not-allowed disabled:opacity-50",
      "data-[placeholder]:text-[color:var(--color-fg-subtle)]",
      className,
    )}
    {...rest}
  >
    {children}
    <SelectPrimitive.Icon asChild>
      <ChevronDown className="h-4 w-4 flex-shrink-0 text-[color:var(--color-fg-muted)]" />
    </SelectPrimitive.Icon>
  </SelectPrimitive.Trigger>
));
SelectTrigger.displayName = "SelectTrigger";

export const SelectContent = forwardRef<
  HTMLDivElement,
  ComponentPropsWithoutRef<typeof SelectPrimitive.Content>
>(({ className, children, position = "popper", ...rest }, ref) => (
  <SelectPrimitive.Portal>
    <SelectPrimitive.Content
      ref={ref}
      position={position}
      className={cn(
        "z-[var(--z-dropdown)] min-w-[8rem] overflow-hidden",
        "rounded-[var(--radius)] border border-[color:var(--color-border)]",
        "bg-[color:var(--color-bg-elevated)] shadow-md",
        "data-[state=open]:animate-[scale-in_var(--motion-quick)_var(--ease-smooth)]",
        "data-[state=closed]:animate-[fade-out_var(--motion-quick)_var(--ease-smooth)]",
        position === "popper" &&
          "data-[side=bottom]:translate-y-1 data-[side=top]:-translate-y-1 data-[side=left]:-translate-x-1 data-[side=right]:translate-x-1",
        className,
      )}
      {...rest}
    >
      <SelectPrimitive.Viewport
        className={cn(
          "p-1",
          // NOTE: do NOT clamp height to `--radix-select-trigger-height` — that's
          // the trigger's own row height (~36px) and would crop the popover to a
          // single visible item, which is exactly the bug we hit on the
          // visibility selector. Width-match the trigger; let height grow.
          position === "popper" && "w-full min-w-[var(--radix-select-trigger-width)]",
        )}
      >
        {children}
      </SelectPrimitive.Viewport>
    </SelectPrimitive.Content>
  </SelectPrimitive.Portal>
));
SelectContent.displayName = "SelectContent";

export const SelectLabel = forwardRef<
  HTMLDivElement,
  ComponentPropsWithoutRef<typeof SelectPrimitive.Label>
>(({ className, ...rest }, ref) => (
  <SelectPrimitive.Label
    ref={ref}
    className={cn(
      "px-2 py-1.5 text-2xs font-semibold uppercase tracking-wide text-[color:var(--color-fg-muted)]",
      className,
    )}
    {...rest}
  />
));
SelectLabel.displayName = "SelectLabel";

export const SelectItem = forwardRef<
  HTMLDivElement,
  ComponentPropsWithoutRef<typeof SelectPrimitive.Item>
>(({ className, children, ...rest }, ref) => (
  <SelectPrimitive.Item
    ref={ref}
    className={cn(
      "relative flex w-full cursor-pointer select-none items-center gap-2",
      "rounded-[var(--radius-sm)] py-1.5 pl-7 pr-2 text-sm",
      "text-[color:var(--color-fg)] outline-none",
      "focus:bg-[color:var(--color-bg-hover)]",
      "data-[highlighted]:bg-[color:var(--color-bg-hover)]",
      "data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
      className,
    )}
    {...rest}
  >
    <span className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center">
      <SelectPrimitive.ItemIndicator>
        <Check className="h-3.5 w-3.5 text-[color:var(--color-accent)]" />
      </SelectPrimitive.ItemIndicator>
    </span>
    <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
  </SelectPrimitive.Item>
));
SelectItem.displayName = "SelectItem";

export const SelectSeparator = forwardRef<
  HTMLDivElement,
  ComponentPropsWithoutRef<typeof SelectPrimitive.Separator>
>(({ className, ...rest }, ref) => (
  <SelectPrimitive.Separator
    ref={ref}
    className={cn("-mx-1 my-1 h-px bg-[color:var(--color-border-subtle)]", className)}
    {...rest}
  />
));
SelectSeparator.displayName = "SelectSeparator";
