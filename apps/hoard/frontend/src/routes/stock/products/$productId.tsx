import { createFileRoute } from "@tanstack/react-router";

import { ProductPage } from "@/features/products/ProductPage";

export const Route = createFileRoute("/stock/products/$productId")({ component: Page });

function Page() {
  const { productId } = Route.useParams();
  return <ProductPage productId={Number(productId)} />;
}
