import { cn } from "@/lib/utils";
import { type VariantProps, cva } from "class-variance-authority";
import { type TextareaHTMLAttributes, forwardRef } from "react";

const textareaVariants = cva(
  [
    "flex w-full rounded-[var(--radius)]",
    "bg-[color:var(--color-bg-subtle)]",
    "border border-[color:var(--color-border)]",
    "text-[color:var(--color-fg)] placeholder:text-[color:var(--color-fg-subtle)]",
    "px-3 py-2",
    "transition-colors duration-[var(--motion-quick)] ease-[var(--ease-smooth)]",
    "focus-visible:outline-none focus-visible:border-[color:var(--color-accent)]",
    "focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
    "disabled:cursor-not-allowed disabled:opacity-50",
    "font-sans text-sm leading-relaxed",
    "resize-none",
  ].join(" "),
  {
    variants: {
      invalid: {
        true: "border-[color:var(--color-danger)] focus-visible:border-[color:var(--color-danger)] focus-visible:ring-[color:var(--color-danger)]/30",
        false: "",
      },
    },
    defaultVariants: {
      invalid: false,
    },
  },
);

export type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> &
  VariantProps<typeof textareaVariants>;

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, invalid, rows = 4, ...rest }, ref) => (
    <textarea
      ref={ref}
      rows={rows}
      className={cn(textareaVariants({ invalid }), className)}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  ),
);
Textarea.displayName = "Textarea";
