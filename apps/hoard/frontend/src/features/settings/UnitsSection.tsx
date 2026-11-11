import { useTranslation } from "react-i18next";

import {
  createSettingsUnit,
  deleteSettingsUnit,
  getListSettingsUnitsQueryKey,
  updateSettingsUnit,
  useListSettingsUnits,
} from "@/api/generated/settings/settings";
import { NamedList } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";

/** The units stock is counted and bought in. */
export function UnitsSection() {
  const { t } = useTranslation("catalog");
  return (
    <SettingsSection id="units" title={t("settings.units.title")}>
      <NamedList
        words="units"
        items={useListSettingsUnits().data}
        queryKey={getListSettingsUnitsQueryKey()}
        add={(name) => createSettingsUnit({ name })}
        rename={(unit, name) => updateSettingsUnit(unit.id, { name })}
        remove={(unit) => deleteSettingsUnit(unit.id)}
      />
    </SettingsSection>
  );
}
