import { useTranslation } from "react-i18next";

import type { Channel } from "@/api/generated/hoard.schemas";

/** What a store goes by: what it calls itself, else its address, else its channel. */
export function useStoreName(): (store: {
  channel: Channel;
  account_label?: string | null;
  shop_domain?: string | null;
}) => string {
  const { t } = useTranslation("stores");
  return (store) => store.account_label || store.shop_domain || t(`channels.${store.channel}`);
}

/** A reason a store or a push keeps, `CODE` or `CODE: detail`, in the reader's
 *  words; one Hoard has no words for reads as it is kept. */
export function useStoreReason(): (kept: string) => string {
  const { t } = useTranslation("stores");
  return (kept) => {
    const [code, ...detail] = kept.split(": ");
    return t(`errors.${code}`, { detail: detail.join(": "), defaultValue: kept });
  };
}
