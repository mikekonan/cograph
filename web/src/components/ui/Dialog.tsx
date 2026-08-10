import { cn } from "@/lib/utils";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { type ComponentPropsWithoutRef, type ReactNode, forwardRef } from "react";

/**
 * Dialog — modal overlay for confirmations, forms, detail views.
 * Uses Radix for focus trap, Esc-to-close, scroll lock, a11y.
 *
 * Compose with: <Dialog><DialogTrigger>…</DialogTrigger><DialogContent>…</DialogContent></Dialog>
 * The Content component already includes DialogOverlay and a close button.
 */

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export const DialogContent = forwardRef<
  HTMLDivElement,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & {
    /** Hide the built-in close (X) button when you want a custom footer. */
    hideCloseButton?: boolean;
  }
>(({ className, children, hideCloseButton, ...rest }, ref) => (
  <DialogPrimitive.Portal>
    <DialogPrimitive.Overlay
      className={cn(
        "fixed inset-0 z-[var(--z-modal)]",
        "bg-[color:var(--color-bg-backdrop)] backdrop-blur-sm",
        "data-[state=open]:animate-[fade-in_var(--motion-base)_var(--ease-smooth)]",
        "data-[state=closed]:animate-[fade-out_var(--motion-quick)_var(--ease-smooth)]",
      )}
    />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        "fixed left-1/2 top-1/2 z-[var(--z-modal)] -translate-x-1/2 -translate-y-1/2",
        "w-[92vw] max-w-lg max-h-[85vh] overflow-auto",
        "rounded-[var(--radius-lg)] border border-[color:var(--color-border)]",
        "bg-[color:var(--color-bg-elevated)] shadow-lg",
        "p-6",
        "data-[state=open]:animate-[scale-in_var(--motion-base)_var(--ease-smooth)]",
        "data-[state=closed]:animate-[scale-out_var(--motion-quick)_var(--ease-smooth)]",
        "focus-visible:outline-none",
        className,
      )}
      {...rest}
    >
      {children}
      {!hideCloseButton && (
        <DialogPrimitive.Close
          className={cn(
            "absolute right-4 top-4 rounded-[var(--radius-sm)] p-1.5",
            "text-[color:var(--color-fg-muted)]",
            "hover:bg-[color:var(--color-bg-hover)] hover:text-[color:var(--color-fg)]",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
            "transition-colors duration-[var(--motion-quick)]",
          )}
          aria-label="Close"
        >
          <X className="h-4 w-4" />
        </DialogPrimitive.Close>
      )}
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>
));
DialogContent.displayName = "DialogContent";

export function DialogHeader({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("mb-4 flex flex-col gap-1.5", className)}>{children}</div>;
}

export const DialogTitle = forwardRef<
  HTMLHeadingElement,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...rest }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn("text-lg font-semibold tracking-tight text-[color:var(--color-fg)]", className)}
    {...rest}
  />
));
DialogTitle.displayName = "DialogTitle";

export const DialogDescription = forwardRef<
  HTMLParagraphElement,
  ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...rest }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn("text-sm text-[color:var(--color-fg-muted)]", className)}
    {...rest}
  />
));
DialogDescription.displayName = "DialogDescription";

export function DialogFooter({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end sm:gap-2",
        className,
      )}
    >
      {children}
    </div>
  );
}
