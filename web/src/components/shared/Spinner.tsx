import { cn } from "@/lib/utils";

type SpinnerProps = {
  /** `sm`=12, `md`=16, `lg`=24px. */
  size?: "sm" | "md" | "lg";
  /** Accessible label for screen readers. Falls back to "Loading". */
  label?: string;
  className?: string;
};

const sizeMap = { sm: "h-3 w-3", md: "h-4 w-4", lg: "h-6 w-6" } as const;

export function Spinner({ size = "md", label = "Loading", className }: SpinnerProps) {
  return (
    <span
      role="status"
      aria-label={label}
      className={cn(
        "inline-block rounded-full border-2 border-current border-r-transparent",
        "animate-spin text-[color:var(--color-accent)]",
        sizeMap[size],
        className,
      )}
    />
  );
}
