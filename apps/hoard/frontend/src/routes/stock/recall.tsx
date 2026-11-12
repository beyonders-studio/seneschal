import { createFileRoute } from "@tanstack/react-router";

import { RecallPage } from "@/features/recall/RecallPage";

/** A recall opened from a lot's page names the lot. */
export interface RecallSearch {
  lot?: number;
}

export const Route = createFileRoute("/stock/recall")({
  component: RecallPage,
  validateSearch: (search: Record<string, unknown>): RecallSearch => {
    const lot = Number(search.lot);
    return Number.isInteger(lot) && lot > 0 ? { lot } : {};
  },
});
