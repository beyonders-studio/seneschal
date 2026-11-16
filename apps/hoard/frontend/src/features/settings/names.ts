import { useTranslation } from "react-i18next";

import type {
  CategoryKind,
  SettingsLocationRead,
  SettingsReasonRead,
  SettingsTypeRead,
  SettingsUnitRead,
} from "@/api/generated/hoard.schemas";
import {
  useListSettingsCategories,
  useListSettingsLocations,
  useListSettingsTypes,
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

/** A type's name: a default type's, until someone names it, is the words
 *  for its kind's default type. */
export function useTypeName(): (type: SettingsTypeRead) => string {
  const { t } = useTranslation("catalog");
  return (type) => type.name ?? t(`settings.types.defaults.${type.kind}`);
}

/** A unit's name where a quantity shows it: a standard unit's is the words
 *  for its key, such as "g"; a unit of the community's own, its name. */
export function useUnitName(): (unit: SettingsUnitRead) => string {
  const { t } = useTranslation();
  return (unit) => (unit.standard ? t(`units.short.${unit.standard}`) : unit.name);
}

/**
 * The organization's units, categories, types and locations, and each one's
 * name by its id: blank until they load. A standard unit and a default type
 * are named in the reader's words.
 */
export function useSettingsNames() {
  const unitName = useUnitName();
  const units = (useListSettingsUnits().data ?? []).map((unit) => ({
    ...unit,
    name: unitName(unit),
  }));
  const categories = useListSettingsCategories().data ?? [];
  const typeName = useTypeName();
  const types = (useListSettingsTypes().data ?? []).map((type) => ({
    ...type,
    name: typeName(type),
  }));
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
    /** A kind's types, its default first. */
    typesOf: (kind: CategoryKind) => types.filter((type) => type.kind === kind),
    unit: named(units),
    category: named(categories),
    type: named(types),
    location: named(locations),
  };
}
