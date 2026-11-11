import { useTranslation } from "react-i18next";

import { numberFormat } from "@/ui/lib/intl";

/** The most places a quantity is shown to: as many as Hoard keeps. */
const PLACES = 6;

/** Formats a quantity, an exact decimal as the API sends it, in the reader's language. */
export function useQuantity(): (amount: string) => string {
  const { i18n } = useTranslation();
  const format = numberFormat(i18n.resolvedLanguage, { maximumFractionDigits: PLACES });
  return (amount) => format.format(amount as Intl.StringNumericLiteral);
}

/** Whether `onHand` is at or below a low-stock limit, when there is one. */
export const isLow = (onHand: string, limit: string | null): boolean =>
  limit !== null && Number(onHand) <= Number(limit);

/** What was typed in a field that may be left blank, for the API: null when it was. */
export const orNull = (text: string): string | null => text.trim() || null;
