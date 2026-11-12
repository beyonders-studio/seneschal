import { createFileRoute } from "@tanstack/react-router";

import { RunPage } from "@/features/runs/RunPage";

export const Route = createFileRoute("/production/runs/$runId")({ component: Page });

function Page() {
  const { runId } = Route.useParams();
  return <RunPage runId={Number(runId)} />;
}
