import { createFileRoute } from "@tanstack/react-router";

import { LotPage } from "@/features/lots/LotPage";

export const Route = createFileRoute("/stock/lots/$lotId")({ component: Page });

function Page() {
  const { lotId } = Route.useParams();
  return <LotPage lotId={Number(lotId)} />;
}
