import { createFileRoute } from "@tanstack/react-router";

import { PurchasePage } from "@/features/purchases/PurchasePage";

export const Route = createFileRoute("/stock/purchases/$purchaseId")({ component: Page });

function Page() {
  const { purchaseId } = Route.useParams();
  return <PurchasePage purchaseId={Number(purchaseId)} />;
}
