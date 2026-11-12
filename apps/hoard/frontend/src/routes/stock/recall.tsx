import { createFileRoute } from "@tanstack/react-router";

import { RecallPage } from "@/features/recall/RecallPage";

export const Route = createFileRoute("/stock/recall")({ component: RecallPage });
