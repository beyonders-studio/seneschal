import { useTranslation } from "react-i18next";

import { useReadSettings } from "@/api/generated/settings/settings";
import { numberFormat } from "@/ui/lib/intl";

/** The most places a cost is shown to: as many as Hoard keeps. */
const EXACT_PLACES = 6;

/**
 * `amount`, an exact decimal as the API sends it, as money in `currency` (an
 * ISO 4217 code) for `locale`. A total keeps the currency's own places; an
 * `exact` amount, such as a unit cost, shows as many as it holds.
 */
export const formatMoney = (
  amount: string,
  currency: string,
  locale?: string,
  { exact = false }: { exact?: boolean } = {}
): string =>
  numberFormat(locale, {
    style: "currency",
    currency,
    ...(exact ? { maximumFractionDigits: EXACT_PLACES } : {}),
  }).format(amount as Intl.StringNumericLiteral);

/**
 * Formats money in the organization's currency, in the reader's language.
 * Until the currency is read, an amount shows as a plain number.
 */
export function useMoney(): (amount: string, options?: { exact?: boolean }) => string {
  const { i18n } = useTranslation();
  const { data } = useReadSettings();
  const locale = i18n.resolvedLanguage;
  return (amount, options) =>
    data
      ? formatMoney(amount, data.currency, locale, options)
      : numberFormat(locale, {
          maximumFractionDigits: options?.exact ? EXACT_PLACES : 2,
        }).format(amount as Intl.StringNumericLiteral);
}
