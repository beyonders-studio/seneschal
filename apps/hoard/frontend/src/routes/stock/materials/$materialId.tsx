import { createFileRoute } from "@tanstack/react-router";

import { MaterialPage } from "@/features/materials/MaterialPage";

export const Route = createFileRoute("/stock/materials/$materialId")({ component: Page });

function Page() {
  const { materialId } = Route.useParams();
  return <MaterialPage materialId={Number(materialId)} />;
}
