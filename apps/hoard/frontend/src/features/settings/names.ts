import { useTranslation } from "react-i18next";

import type {
  CategoryKind,
  SettingsLocationRead,
  SettingsReasonRead,
  SettingsUnitRead,
} from "@/api/generated/hoard.schemas";
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

/** A reason's name: a default reason's, until someone names it, is the
 *  words for its key. */
export function useReasonName(): (reason: SettingsReasonRead) => string {
  const { t } = useTranslation("catalog");
  return (reason) =>
    reason.name ?? (reason.key ? t(`settings.reasons.defaults.${reason.key}`) : "");
}

/** A unit's name where a quantity shows it: a standard unit's is the words
 *  for its key, such as "g"; a unit of the community's own, its name. */
export function useUnitName(): (unit: SettingsUnitRead) => string {
  const { t } = useTranslation();
  return (unit) => (unit.standard ? t(`units.short.${unit.standard}`) : unit.name);
}

/**
 * The organization's units, categories and locations, and each one's name by
 * its id: blank until they load. A standard unit is named in the reader's words.
 */
export function useSettingsNames() {
  const unitName = useUnitName();
  const units = (useListSettingsUnits().data ?? []).map((unit) => ({
    ...unit,
    name: unitName(unit),
  }));
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
