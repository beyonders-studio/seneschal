import { createFileRoute } from "@tanstack/react-router";

import { StocksmithImportPage } from "@/features/imports/StocksmithImportPage";

/** Settings › Import, for the community's admins. */
export const Route = createFileRoute("/settings_/import")({ component: StocksmithImportPage });
