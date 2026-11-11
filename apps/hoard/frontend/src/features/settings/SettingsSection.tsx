import type { ReactNode } from "react";

/** One section of Settings: its heading, then what it holds. */
export function SettingsSection({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <section aria-labelledby={`settings-${id}`} className="flex flex-col gap-3">
      <h2 id={`settings-${id}`} className="text-lg font-semibold tracking-tight">
        {title}
      </h2>
      {children}
    </section>
  );
}
