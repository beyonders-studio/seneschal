import { createFileRoute } from "@tanstack/react-router";

import { BasePage } from "@/features/bases/BasePage";

export const Route = createFileRoute("/stock/bases/$baseId")({ component: Page });

function Page() {
  const { baseId } = Route.useParams();
  return <BasePage baseId={Number(baseId)} />;
}
