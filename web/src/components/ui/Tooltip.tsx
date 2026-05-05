import { cn } from "@/lib/utils";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { type ComponentPropsWithoutRef, type ReactNode, forwardRef } from "react";

/**
 * Tooltip — hover-triggered hint. Uses Radix for positioning + a11y.
 * A single `<TooltipProvider>` should wrap the app root (see App.tsx).
 */

export const TooltipProvider = TooltipPrimitive.Provider;
export const TooltipRoot = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;

export const TooltipContent = forwardRef<
  HTMLDivElement,
  ComponentPropsWithoutRef<typeof TooltipPrimitive.Content>
>(({ className, sideOffset = 6, children, ...rest }, ref) => (
  <TooltipPrimitive.Portal>
    <TooltipPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        "z-[var(--z-overlay)]",
        "rounded-[var(--radius-sm)] px-2 py-1",
        "bg-[color:var(--color-bg-elevated)] text-[color:var(--color-fg)]",
        "border border-[color:var(--color-border)]",
        "text-xs font-medium",
        "shadow-md",
        "data-[state=delayed-open]:animate-[fade-in_var(--motion-quick)_var(--ease-smooth)]",
        "data-[state=closed]:animate-[fade-out_var(--motion-quick)_var(--ease-smooth)]",
        className,
      )}
      {...rest}
    >
      {children}
    </TooltipPrimitive.Content>
  </TooltipPrimitive.Portal>
));
TooltipContent.displayName = "TooltipContent";

/**
 * Convenience wrapper for the common case: trigger + string content.
 * Use the low-level parts above when you need custom content (kbd hints, etc).
 */
export function Tooltip({
  content,
  children,
  side = "top",
  delayDuration = 250,
}: {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  delayDuration?: number;
}) {
  return (
    <TooltipRoot delayDuration={delayDuration}>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side}>{content}</TooltipContent>
    </TooltipRoot>
  );
}
