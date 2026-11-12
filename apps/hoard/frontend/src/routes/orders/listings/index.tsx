import { createFileRoute } from "@tanstack/react-router";

import { ListingsPage } from "@/features/listings/ListingsPage";

export const Route = createFileRoute("/orders/listings/")({ component: ListingsPage });
