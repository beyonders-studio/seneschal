import { createFileRoute } from "@tanstack/react-router";

import { BasesPage } from "@/features/bases/BasesPage";

export const Route = createFileRoute("/stock/bases/")({ component: BasesPage });
