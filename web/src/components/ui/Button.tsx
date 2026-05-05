import { cn } from "@/lib/utils";
import { type VariantProps, cva } from "class-variance-authority";
import type { ButtonHTMLAttributes } from "react";
import { forwardRef } from "react";

export const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-2",
    "rounded-[var(--radius)] font-medium",
    "transition-colors duration-[var(--motion-quick)] ease-[var(--ease-smooth)]",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-bg)]",
    "disabled:pointer-events-none disabled:opacity-50",
    "whitespace-nowrap",
  ].join(" "),
  {
    variants: {
      variant: {
        primary: [
          "bg-[color:var(--color-accent)] text-[color:var(--color-accent-fg)]",
          "hover:bg-[color:var(--color-accent-hover)]",
          "active:bg-[color:var(--color-accent-pressed)]",
        ].join(" "),
        secondary: [
          "bg-[color:var(--color-bg-surface)] text-[color:var(--color-fg)]",
          "border border-[color:var(--color-border)]",
          "hover:bg-[color:var(--color-bg-hover)]",
        ].join(" "),
        ghost: [
          "bg-transparent text-[color:var(--color-fg)]",
          "hover:bg-[color:var(--color-bg-hover)]",
        ].join(" "),
        danger: [
          "bg-[color:var(--color-danger)] text-[color:var(--color-danger-fg)]",
          "hover:brightness-110 active:brightness-95",
        ].join(" "),
        link: [
          "bg-transparent p-0 text-[color:var(--color-accent)] underline-offset-4",
          "hover:underline",
        ].join(" "),
      },
      size: {
        sm: "h-8 px-3 text-sm",
        md: "h-9 px-3.5 text-sm",
        lg: "h-10 px-4 text-base",
        icon: "h-9 w-9 p-0",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "md",
    },
  },
);

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants>;

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, type = "button", ...rest }, ref) => (
    <button
      ref={ref}
      type={type}
      className={cn(buttonVariants({ variant, size }), className)}
      {...rest}
    />
  ),
);
Button.displayName = "Button";
