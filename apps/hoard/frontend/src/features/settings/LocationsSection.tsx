import { useTranslation } from "react-i18next";

import {
  createSettingsLocation,
  deleteSettingsLocation,
  getListSettingsLocationsQueryKey,
  updateSettingsLocation,
  useListSettingsLocations,
} from "@/api/generated/settings/settings";
import { NamedList } from "@/features/settings/NamedList";
import { useLocationName } from "@/features/settings/names";
import { SettingsSection } from "@/features/settings/SettingsSection";

/** The places stock is kept: the main location, which stays, and the rest. */
export function LocationsSection() {
  const { t } = useTranslation("catalog");
  return (
    <SettingsSection id="locations" title={t("settings.locations.title")}>
      <NamedList
        words="locations"
        items={useListSettingsLocations().data}
        queryKey={getListSettingsLocationsQueryKey()}
        nameOf={useLocationName()}
        add={(name) => createSettingsLocation({ name })}
        rename={(location, name) => updateSettingsLocation(location.id, { name })}
        remove={(location) => deleteSettingsLocation(location.id)}
        removable={(location) => !location.main}
      />
    </SettingsSection>
  );
}
