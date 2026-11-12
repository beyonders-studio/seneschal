import { useTranslation } from "react-i18next";

import type { LotRead } from "@/api/generated/hoard.schemas";

/** A lot's trace, on its page: back to what went into it, and forward to
 *  what was made from it and where it went. */
export function TraceSection(_props: { lot: LotRead }) {
  const { t } = useTranslation("trace");
  return <p className="text-sm text-muted-foreground">{t("section.empty")}</p>;
}
