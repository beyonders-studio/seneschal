import { createFileRoute, redirect } from "@tanstack/react-router";

/** Production opens on its runs. */
export const Route = createFileRoute("/production/")({
  beforeLoad: () => {
    throw redirect({ to: "/production/runs", replace: true });
  },
});
