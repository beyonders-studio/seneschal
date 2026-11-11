import { createFileRoute } from "@tanstack/react-router";

import { ProductsPage } from "@/features/products/ProductsPage";

export const Route = createFileRoute("/stock/products/")({ component: ProductsPage });
