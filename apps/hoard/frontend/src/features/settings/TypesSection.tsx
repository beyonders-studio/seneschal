import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import { CategoryKind, type SettingsTypeRead, TypeShape } from "@/api/generated/hoard.schemas";
import {
  createSettingsType,
  deleteSettingsType,
  getListSettingsCategoriesQueryKey,
  getListSettingsTypesQueryKey,
  updateSettingsType,
  useListSettingsTypes,
} from "@/api/generated/settings/settings";
import { NamedList } from "@/features/settings/NamedList";
import { useTypeName } from "@/features/settings/names";
import { SettingChoice } from "@/features/settings/SettingChoice";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { errorMessage } from "@/lib/errors";
import { Badge } from "@/ui/badge";

/** The shapes each kind's types take, the first its default type's, as the
 *  backend's `SHAPES` holds them. */
const SHAPES: Record<CategoryKind, TypeShape[]> = {
  material: [TypeShape.ingredient, TypeShape.packaging],
  base: [TypeShape.base],
  product: [TypeShape.made, TypeShape.bought, TypeShape.bundle],
};

/** The types of material, base and product, each kind its own, with the
 *  shape each one's page is drawn as until it is laid out. A kind's default
 *  type stays. */
export function TypesSection() {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const types = useListSettingsTypes().data;
  const typeName = useTypeName();
  const queryKey = getListSettingsTypesQueryKey();
  const shape = (type: SettingsTypeRead) => (
    <div className="flex flex-wrap items-center justify-end gap-2">
      {type.is_default && <Badge variant="outline">{t("settings.types.default")}</Badge>}
      {SHAPES[type.kind].length > 1 && (
        <SettingChoice
          label={t("settings.types.shape", { name: typeName(type) })}
          value={type.shape}
          choices={SHAPES[type.kind].map((each) => ({
            value: each,
            label: t(`settings.types.shapes.${each}`),
          }))}
          save={(chosen) => updateSettingsType(type.id, { shape: chosen })}
          queryKey={queryKey}
          failed={(error) => errorMessage(error, t, "settings.saveFailed")}
        />
      )}
    </div>
  );
  return (
    <SettingsSection id="types" title={t("settings.types.title")}>
      <p className="text-sm text-muted-foreground">{t("settings.types.hint")}</p>
      {Object.values(CategoryKind).map((kind) => (
        <div key={kind} className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">{t(`settings.types.${kind}`)}</h3>
          <NamedList
            words={`types.${kind}s`}
            items={types?.filter((type) => type.kind === kind)}
            queryKey={queryKey}
            nameOf={typeName}
            add={(name) => createSettingsType({ kind, name })}
            rename={(type, name) => updateSettingsType(type.id, { name })}
            remove={async (type) => {
              await deleteSettingsType(type.id);
              // The categories naming it as their default no longer do.
              await queryClient.invalidateQueries({
                queryKey: getListSettingsCategoriesQueryKey(),
              });
            }}
            removable={(type) => !type.is_default}
            detail={shape}
          />
        </div>
      ))}
    </SettingsSection>
  );
}
