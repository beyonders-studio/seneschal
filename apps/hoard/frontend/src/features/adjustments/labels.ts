import { useTranslation } from "react-i18next";

import type { AdjustmentItem } from "@/api/generated/hoard.schemas";
import { useListSettingsReasons } from "@/api/generated/settings/settings";
import { useReasonName, useSettingsNames } from "@/features/settings/names";
import { dateTimeFormat, listFormat, numberFormat } from "@/ui/lib/intl";

/** The most places a quantity is shown to: as many as Hoard keeps. */
const QUANTITY_PLACES = 6;

/**
 * How purchases, adjustments and stocktakes name what they hold, in the
 * reader's language: items, locations, reasons and categories (from the
 * settings), quantities and dates.
 */
export function useMovementLabels() {
  const { t, i18n } = useTranslation("movements");
  const locale = i18n.resolvedLanguage;
  const names = useSettingsNames();
  const reasons = useListSettingsReasons().data ?? [];
  const reasonName = useReasonName();

  const item = (named: Pick<AdjustmentItem, "name" | "attributes">) => {
    const values = Object.values(named.attributes);
    return values.length
      ? t("item.variation", {
          name: named.name,
          attributes: listFormat(locale, { type: "unit" }).format(values),
        })
      : named.name;
  };
  const reason = (id: number | null | undefined) => {
    const found = reasons.find((named) => named.id === id);
    return found ? reasonName(found) : "";
  };
  const quantity = (amount: string) =>
    numberFormat(locale, { maximumFractionDigits: QUANTITY_PLACES }).format(
      amount as Intl.StringNumericLiteral
    );
  const date = (day: string) =>
    dateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(`${day}T00:00:00`));

  return {
    item,
    location: names.location,
    reason,
    category: names.category,
    quantity,
    date,
  };
}

/** Today, as the API's dates are written. */
export const today = (): string => {
  const now = new Date();
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};
