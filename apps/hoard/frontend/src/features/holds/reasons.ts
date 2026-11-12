import { useTranslation } from "react-i18next";

import type { HoldReasonRead } from "@/api/generated/hoard.schemas";
import { useListHoldReasons } from "@/api/generated/holds/holds";

/** A hold reason's name: a default reason's, until someone names it, is the
 *  words for its key. */
export function useHoldReasonName(): (reason: HoldReasonRead) => string {
  const { t } = useTranslation("holds");
  return (reason) =>
    reason.name ??
    (reason.key ? t(`reasons.defaults.${reason.key}`, { defaultValue: reason.key }) : "");
}

/** The organization's hold reasons as choices, each by its name. */
export function useHoldReasonOptions() {
  const name = useHoldReasonName();
  const reasons = useListHoldReasons().data ?? [];
  return {
    reasons,
    options: reasons.map((reason) => ({ id: reason.id, name: name(reason) })),
    name,
  };
}
