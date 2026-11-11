import { createFileRoute } from "@tanstack/react-router";

import { MaterialsPage } from "@/features/materials/MaterialsPage";

export const Route = createFileRoute("/stock/materials/")({ component: MaterialsPage });
