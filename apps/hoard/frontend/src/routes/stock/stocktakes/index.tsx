import { createFileRoute } from "@tanstack/react-router";

import { StocktakesPage } from "@/features/stocktakes/StocktakesPage";

export const Route = createFileRoute("/stock/stocktakes/")({ component: StocktakesPage });
