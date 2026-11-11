import { createFileRoute, redirect } from "@tanstack/react-router";

/** Stock opens on its materials. */
export const Route = createFileRoute("/stock/")({
  beforeLoad: () => {
    throw redirect({ to: "/stock/materials", replace: true });
  },
});
