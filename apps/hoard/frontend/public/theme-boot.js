// Runs before first paint: the OS appearance, until the handoff from
// Initiative brings the reader's own (src/lib/theme.ts). A file rather than an
// inline script, so a `script-src 'self'` policy allows it.
try {
  if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
    document.documentElement.classList.add("dark");
  }
} catch {
  // No matchMedia: stay light.
}
