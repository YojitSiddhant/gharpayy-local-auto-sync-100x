import type { InputHTMLAttributes } from "react";

// Same class recipe as the reference's shadcn Input. The reference merges classes with tailwind-merge,
// so a caller's height (e.g. h-8) replaces the default h-9; mirror that for height without the dependency.
const base = "flex w-full rounded-md border border-input bg-transparent px-3 py-1 text-base shadow-sm transition-colors placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 md:text-sm";

export function Input({ className = "", ...props }: InputHTMLAttributes<HTMLInputElement>) {
  const height = /(^|\s)h-\S+/.test(className) ? "" : "h-9";
  return <input className={`${base} ${height} ${className}`} {...props} />;
}
