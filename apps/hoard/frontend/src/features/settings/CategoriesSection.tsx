import { useTranslation } from "react-i18next";

import { CategoryKind } from "@/api/generated/hoard.schemas";
import {
  createSettingsCategory,
  deleteSettingsCategory,
  getListSettingsCategoriesQueryKey,
  updateSettingsCategory,
  useListSettingsCategories,
} from "@/api/generated/settings/settings";
import { NamedList } from "@/features/settings/NamedList";
import { PickingField } from "@/features/settings/PickingField";
import { SettingsSection } from "@/features/settings/SettingsSection";

/** The categories materials and products are sorted into, each kind its own,
 *  with which of each one's lots goes first. */
export function CategoriesSection() {
  const { t } = useTranslation("catalog");
  const categories = useListSettingsCategories().data;
  return (
    <SettingsSection id="categories" title={t("settings.categories.title")}>
      <p className="text-sm text-muted-foreground">{t("holds:picking.hint")}</p>
      {Object.values(CategoryKind).map((kind) => (
        <div key={kind} className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{t(`settings.categories.${kind}`)}</h3>
          <NamedList
            words={`categories.${kind}s`}
            items={categories?.filter((category) => category.kind === kind)}
            queryKey={getListSettingsCategoriesQueryKey()}
            add={(name) => createSettingsCategory({ kind, name })}
            rename={(category, name) => updateSettingsCategory(category.id, { name })}
            remove={(category) => deleteSettingsCategory(category.id)}
            detail={(category) => <PickingField category={category} />}
          />
        </div>
      ))}
    </SettingsSection>
  );
}
