import { createFileRoute } from "@tanstack/react-router";

import { PurchasesPage } from "@/features/purchases/PurchasesPage";

export const Route = createFileRoute("/stock/purchases/")({ component: PurchasesPage });
