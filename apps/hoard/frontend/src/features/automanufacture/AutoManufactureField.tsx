import { useTranslation } from "react-i18next";

import type { ProductDetail } from "@/api/generated/hoard.schemas";
import { SettingsSection } from "@/features/settings/SettingsSection";

/** Whether a store order makes what it sells: a product's own choice on its
 *  page, or, with no product, the organization's in Settings. */
export function AutoManufactureField({ product }: { product?: ProductDetail }) {
  const { t } = useTranslation("sync");
  if (product === undefined) {
    return <SettingsSection id="auto-manufacture" title={t("autoManufacture.title")} />;
  }
  return <p className="text-sm text-muted-foreground">{t("autoManufacture.inherits")}</p>;
}
