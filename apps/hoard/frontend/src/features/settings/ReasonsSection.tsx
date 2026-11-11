import { useTranslation } from "react-i18next";

import {
  createSettingsReason,
  deleteSettingsReason,
  getListSettingsReasonsQueryKey,
  updateSettingsReason,
  useListSettingsReasons,
} from "@/api/generated/settings/settings";
import { NamedList } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";

/** The reasons stock is adjusted by hand. */
export function ReasonsSection() {
  const { t } = useTranslation("catalog");
  return (
    <SettingsSection id="reasons" title={t("settings.reasons.title")}>
      <NamedList
        words="reasons"
        items={useListSettingsReasons().data}
        queryKey={getListSettingsReasonsQueryKey()}
        add={(name) => createSettingsReason({ name })}
        rename={(reason, name) => updateSettingsReason(reason.id, { name })}
        remove={(reason) => deleteSettingsReason(reason.id)}
      />
    </SettingsSection>
  );
}
