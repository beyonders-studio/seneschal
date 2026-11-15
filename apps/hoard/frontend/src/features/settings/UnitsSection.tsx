import { useTranslation } from "react-i18next";

import {
  createSettingsUnit,
  deleteSettingsUnit,
  getListSettingsUnitsQueryKey,
  updateSettingsUnit,
  useListSettingsUnits,
} from "@/api/generated/settings/settings";
import { NamedList } from "@/features/settings/NamedList";
import { useUnitName } from "@/features/settings/names";
import { SettingsSection } from "@/features/settings/SettingsSection";

/** The units stock is counted and bought in: Hoard's standard units, which
 *  stay as they are, and the community's own. */
export function UnitsSection() {
  const { t } = useTranslation("catalog");
  const unitName = useUnitName();
  return (
    <SettingsSection id="units" title={t("settings.units.title")}>
      <NamedList
        words="units"
        items={useListSettingsUnits().data}
        queryKey={getListSettingsUnitsQueryKey()}
        nameOf={(unit) => (unit.standard ? t(`hoard:units.names.${unit.standard}`) : unit.name)}
        fixed={(unit) => unit.standard !== null}
        detail={(unit) =>
          unit.standard && (
            <span className="text-sm text-muted-foreground">
              {unitName(unit)} · {t("settings.units.standard")}
            </span>
          )
        }
        add={(name) => createSettingsUnit({ name })}
        rename={(unit, name) => updateSettingsUnit(unit.id, { name })}
        remove={(unit) => deleteSettingsUnit(unit.id)}
      />
    </SettingsSection>
  );
}
