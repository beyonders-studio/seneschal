import { createFileRoute } from "@tanstack/react-router";

import { StocktakePage } from "@/features/stocktakes/StocktakePage";

export const Route = createFileRoute("/stock/stocktakes/$stocktakeId")({ component: Page });

function Page() {
  const { stocktakeId } = Route.useParams();
  return <StocktakePage stocktakeId={Number(stocktakeId)} />;
}
