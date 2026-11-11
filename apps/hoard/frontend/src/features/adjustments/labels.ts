import { useTranslation } from "react-i18next";

import { useReadAdjustmentOptions } from "@/api/generated/adjustments/adjustments";
import type { AdjustmentItem } from "@/api/generated/hoard.schemas";
import { dateTimeFormat, listFormat, numberFormat } from "@/ui/lib/intl";

/** The most places a quantity is shown to: as many as Hoard keeps. */
const QUANTITY_PLACES = 6;

/**
 * How purchases, adjustments and stocktakes name what they hold, in the
 * reader's language: items, locations, reasons and categories (from what a
 * movement may name), quantities and dates.
 */
export function useMovementLabels() {
  const { t, i18n } = useTranslation("movements");
  const locale = i18n.resolvedLanguage;
  const { data: options } = useReadAdjustmentOptions();

  const item = (named: Pick<AdjustmentItem, "name" | "attributes">) => {
    const values = Object.values(named.attributes);
    return values.length
      ? t("item.variation", {
          name: named.name,
          attributes: listFormat(locale, { type: "unit" }).format(values),
        })
      : named.name;
  };
  const location = (id: number | null | undefined) => {
    const found = options?.locations.find((place) => place.id === id);
    return found ? (found.name ?? t("hoard:common.mainLocation")) : "";
  };
  const reason = (id: number | null | undefined) =>
    options?.reasons.find((named) => named.id === id)?.name ?? "";
  const category = (id: number | null | undefined) =>
    options?.categories.find((named) => named.id === id)?.name ?? "";
  const quantity = (amount: string) =>
    numberFormat(locale, { maximumFractionDigits: QUANTITY_PLACES }).format(
      amount as Intl.StringNumericLiteral
    );
  const date = (day: string) =>
    dateTimeFormat(locale, { dateStyle: "medium" }).format(new Date(`${day}T00:00:00`));

  return { options, item, location, reason, category, quantity, date };
}

/** Today, as the API's dates are written. */
export const today = (): string => {
  const now = new Date();
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
};
