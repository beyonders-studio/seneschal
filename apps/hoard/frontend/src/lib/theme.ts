/**
 * Hoard wears the reader's appearance in Initiative.
 *
 * The kit's styles carry Initiative's light and dark colour tokens. What
 * Initiative sends over the parent bridge (src/lib/parentBridge.ts) picks the
 * mode, by the `.dark` class, and overlays the reader's own colours on those
 * tokens. Until it says, the OS appearance stands (public/theme-boot.js).
 */

import type { HostTheme } from "@/lib/parentBridge";

/** The kit's colour tokens: the only properties Initiative's colours may set. */
const COLOR_TOKENS = new Set(
  [
    "background",
    "foreground",
    "card",
    "card-foreground",
    "popover",
    "popover-foreground",
    "primary",
    "primary-foreground",
    "secondary",
    "secondary-foreground",
    "muted",
    "muted-foreground",
    "accent",
    "accent-foreground",
    "destructive",
    "success",
    "warning",
    "warning-foreground",
    "border",
    "input",
    "ring",
    "chart-1",
    "chart-2",
    "chart-3",
    "chart-4",
    "chart-5",
    "sidebar",
    "sidebar-foreground",
    "sidebar-primary",
    "sidebar-primary-foreground",
    "sidebar-accent",
    "sidebar-accent-foreground",
    "sidebar-border",
    "sidebar-ring",
  ].map((name) => `--${name}`)
);

/** A colour value: a colour function, a hex value or a name, and nothing else. */
const COLOR_VALUE = /^[a-z0-9#.%\s,()/-]{1,100}$/i;
/** Values that point at another resource are not colours. */
const REFERENCE = /\/\/|url\s*\(|image(-set)?\s*\(/i;

/** The entries that name one of the kit's colour tokens and hold a colour. */
export const colorTokens = (colors: Record<string, string>): Record<string, string> =>
  Object.fromEntries(
    Object.entries(colors).filter(
      ([name, value]) => COLOR_TOKENS.has(name) && COLOR_VALUE.test(value) && !REFERENCE.test(value)
    )
  );

/** The tokens the last call set, so the next replaces them rather than adding to them. */
const applied = new Set<string>();

/** Take Initiative's mode and colours; without colours, the kit's own for that mode. */
export function applyHostTheme(theme: HostTheme, colors?: Record<string, string> | null): void {
  const root = document.documentElement;
  for (const name of applied) root.style.removeProperty(name);
  applied.clear();
  for (const [name, value] of Object.entries(colorTokens(colors ?? {}))) {
    root.style.setProperty(name, value);
    applied.add(name);
  }
  root.classList.toggle("dark", theme === "dark");
}
