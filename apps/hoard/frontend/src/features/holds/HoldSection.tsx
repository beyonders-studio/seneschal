import { useTranslation } from "react-i18next";

import type { LotRead } from "@/api/generated/hoard.schemas";

/** A lot's hold, on its page: its reason and ready date, and putting it on
 *  hold, releasing it or discarding it. */
export function HoldSection({ lot }: { lot: LotRead }) {
  const { t } = useTranslation("holds");
  return (
    <p className="text-sm text-muted-foreground">
      {lot.hold ? t("section.onHold") : t("section.available")}
    </p>
  );
}
