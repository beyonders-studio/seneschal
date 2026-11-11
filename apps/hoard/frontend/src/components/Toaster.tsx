import { useSyncExternalStore } from "react";

import { cn } from "@/ui/lib/utils";

interface Notice {
  id: number;
  kind: "success" | "error";
  message: string;
}

/** How long a notice stays. */
const SHOWN_MS = 5_000;

let notices: Notice[] = [];
let nextId = 0;
const listeners = new Set<() => void>();

const publish = (next: Notice[]) => {
  notices = next;
  for (const listener of listeners) listener();
};

const show = (kind: Notice["kind"], message: string) => {
  const id = nextId++;
  publish([...notices, { id, kind, message }]);
  setTimeout(() => publish(notices.filter((notice) => notice.id !== id)), SHOWN_MS);
};

/** Short notices, which the UI kit shows through its host. */
export const toast = {
  success: (message: string) => show("success", message),
  error: (message: string) => show("error", message),
};

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

/** Where the notices appear: the bottom corner, read out as they arrive. */
export function Toaster() {
  const shown = useSyncExternalStore(subscribe, () => notices);
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed right-4 bottom-4 z-50 flex max-w-sm flex-col gap-2"
    >
      {shown.map((notice) => (
        <div
          key={notice.id}
          role={notice.kind === "error" ? "alert" : "status"}
          className={cn(
            "rounded-md border bg-popover px-4 py-3 text-sm text-popover-foreground shadow-md",
            notice.kind === "error" && "border-destructive/50 text-destructive"
          )}
        >
          {notice.message}
        </div>
      ))}
    </div>
  );
}
