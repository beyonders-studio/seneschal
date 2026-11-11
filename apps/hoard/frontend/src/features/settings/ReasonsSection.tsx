import { useTranslation } from "react-i18next";

import {
  createSettingsReason,
  deleteSettingsReason,
  getListSettingsReasonsQueryKey,
  updateSettingsReason,
  useListSettingsReasons,
} from "@/api/generated/settings/settings";
import { NamedList } from "@/features/settings/NamedList";
import { useReasonName } from "@/features/settings/names";
import { SettingsSection } from "@/features/settings/SettingsSection";

/** The reasons stock is adjusted by hand: the default ones, which people
 *  rename or remove, and their own. */
export function ReasonsSection() {
  const { t } = useTranslation("catalog");
  return (
    <SettingsSection id="reasons" title={t("settings.reasons.title")}>
      <NamedList
        words="reasons"
        items={useListSettingsReasons().data}
        queryKey={getListSettingsReasonsQueryKey()}
        nameOf={useReasonName()}
        add={(name) => createSettingsReason({ name })}
        rename={(reason, name) => updateSettingsReason(reason.id, { name })}
        remove={(reason) => deleteSettingsReason(reason.id)}
      />
    </SettingsSection>
  );
}
