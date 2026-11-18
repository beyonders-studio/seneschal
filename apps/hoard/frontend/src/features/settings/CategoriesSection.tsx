import { useTranslation } from "react-i18next";

import {
  CategoryKind,
  LotScan,
  Picking,
  type SettingsCategoryRead,
} from "@/api/generated/hoard.schemas";
import {
  createSettingsCategory,
  deleteSettingsCategory,
  getListSettingsCategoriesQueryKey,
  updateSettingsCategory,
  useListSettingsCategories,
} from "@/api/generated/settings/settings";
import { NamedList } from "@/features/settings/NamedList";
import { useSettingsNames } from "@/features/settings/names";
import { SettingChoice } from "@/features/settings/SettingChoice";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { errorMessage } from "@/lib/errors";

/** The value that stands for no default type, which a select item cannot carry as empty. */
const NO_TYPE = "none";

/** The categories materials, bases and products are sorted into, each kind
 *  its own, with which of each one's lots goes first and the type a record
 *  made in it takes. */
export function CategoriesSection() {
  const { t } = useTranslation("catalog");
  const categories = useListSettingsCategories().data;
  const { typesOf } = useSettingsNames();
  const queryKey = getListSettingsCategoriesQueryKey();
  const detail = (category: SettingsCategoryRead) => (
    <div className="flex flex-wrap justify-end gap-2">
      <SettingChoice
        label={t("holds:picking.label", { name: category.name })}
        value={category.picking}
        choices={Object.values(Picking).map((rule) => ({
          value: rule,
          label: t(`holds:picking.rules.${rule}`),
        }))}
        save={(picking) => updateSettingsCategory(category.id, { picking })}
        queryKey={queryKey}
        failed={(error) => errorMessage(error, t, "holds:picking.failed")}
      />
      <SettingChoice
        label={t("holds:lotScan.label", { name: category.name })}
        value={category.lot_scan}
        choices={Object.values(LotScan).map((rule) => ({
          value: rule,
          label: t(`holds:lotScan.rules.${rule}`),
        }))}
        save={(lot_scan) => updateSettingsCategory(category.id, { lot_scan })}
        queryKey={queryKey}
        failed={(error) => errorMessage(error, t, "holds:lotScan.failed")}
      />
      <SettingChoice
        label={t("settings.categories.defaultType", { name: category.name })}
        value={category.default_type_id === null ? NO_TYPE : String(category.default_type_id)}
        choices={[
          { value: NO_TYPE, label: t("settings.categories.noDefaultType") },
          ...typesOf(category.kind).map((type) => ({ value: String(type.id), label: type.name })),
        ]}
        save={(chosen) =>
          updateSettingsCategory(category.id, {
            default_type_id: chosen === NO_TYPE ? null : Number(chosen),
          })
        }
        queryKey={queryKey}
        failed={(error) => errorMessage(error, t, "settings.saveFailed")}
      />
    </div>
  );
  return (
    <SettingsSection id="categories" title={t("settings.categories.title")}>
      <p className="text-sm text-muted-foreground">{t("holds:picking.hint")}</p>
      <p className="text-sm text-muted-foreground">{t("holds:lotScan.hint")}</p>
      <p className="text-sm text-muted-foreground">{t("settings.categories.typeHint")}</p>
      {Object.values(CategoryKind).map((kind) => (
        <div key={kind} className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{t(`settings.categories.${kind}`)}</h3>
          <NamedList
            words={`categories.${kind}s`}
            items={categories?.filter((category) => category.kind === kind)}
            queryKey={queryKey}
            add={(name) => createSettingsCategory({ kind, name })}
            rename={(category, name) => updateSettingsCategory(category.id, { name })}
            remove={(category) => deleteSettingsCategory(category.id)}
            detail={detail}
          />
        </div>
      ))}
    </SettingsSection>
  );
}
