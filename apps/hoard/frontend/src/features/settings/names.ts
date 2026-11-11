import { useTranslation } from "react-i18next";

import type { CategoryKind, SettingsLocationRead } from "@/api/generated/hoard.schemas";
import {
  useListSettingsCategories,
  useListSettingsLocations,
  useListSettingsUnits,
} from "@/api/generated/settings/settings";

/** A location's name: the main location's, until someone names it, is the
 *  words for the main location. */
export function useLocationName(): (location: SettingsLocationRead) => string {
  const { t } = useTranslation();
  return (location) => location.name ?? t("common.mainLocation");
}

/**
 * The organization's units, categories and locations, and each one's name by
 * its id: blank until they load.
 */
export function useSettingsNames() {
  const units = useListSettingsUnits().data ?? [];
  const categories = useListSettingsCategories().data ?? [];
  const locationName = useLocationName();
  const locations = (useListSettingsLocations().data ?? []).map((location) => ({
    id: location.id,
    name: locationName(location),
  }));
  const named = (list: { id: number; name: string }[]) => {
    const names = new Map(list.map((record) => [record.id, record.name]));
    return (id: number | null | undefined) => (id == null ? "" : (names.get(id) ?? ""));
  };
  return {
    units,
    locations,
    categoriesOf: (kind: CategoryKind) => categories.filter((category) => category.kind === kind),
    unit: named(units),
    category: named(categories),
    location: named(locations),
  };
}
