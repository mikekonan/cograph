import { cn } from "@/lib/utils";
import { type VariantProps, cva } from "class-variance-authority";
import { type InputHTMLAttributes, forwardRef } from "react";

const inputVariants = cva(
  [
    "flex w-full rounded-[var(--radius)]",
    "bg-[color:var(--color-bg-subtle)]",
    "border border-[color:var(--color-border)]",
    "text-[color:var(--color-fg)] placeholder:text-[color:var(--color-fg-subtle)]",
    "transition-colors duration-[var(--motion-quick)] ease-[var(--ease-smooth)]",
    "focus-visible:outline-none focus-visible:border-[color:var(--color-accent)]",
    "focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)]/40",
    "disabled:cursor-not-allowed disabled:opacity-50",
    "file:border-0 file:bg-transparent file:text-sm file:font-medium",
  ].join(" "),
  {
    variants: {
      size: {
        sm: "h-8 px-2.5 text-sm",
        md: "h-9 px-3 text-sm",
        lg: "h-10 px-3.5 text-base",
      },
      invalid: {
        true: "border-[color:var(--color-danger)] focus-visible:border-[color:var(--color-danger)] focus-visible:ring-[color:var(--color-danger)]/30",
        false: "",
      },
    },
    defaultVariants: {
      size: "md",
      invalid: false,
    },
  },
);

export type InputProps = Omit<InputHTMLAttributes<HTMLInputElement>, "size"> &
  VariantProps<typeof inputVariants>;

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, size, invalid, type = "text", ...rest }, ref) => (
    <input
      ref={ref}
      type={type}
      className={cn(inputVariants({ size, invalid }), className)}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  ),
);
Input.displayName = "Input";
