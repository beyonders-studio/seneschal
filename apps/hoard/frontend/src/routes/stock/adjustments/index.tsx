import { createFileRoute } from "@tanstack/react-router";

import { AdjustmentsPage } from "@/features/adjustments/AdjustmentsPage";

export const Route = createFileRoute("/stock/adjustments/")({ component: AdjustmentsPage });
