import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  AutoManufacture,
  type ProductDetail,
  type ProductVariationRead,
} from "@/api/generated/hoard.schemas";
import {
  getListProductsQueryKey,
  getReadProductQueryKey,
  updateProduct,
  updateProductVariation,
} from "@/api/generated/products/products";
import {
  getReadSettingsQueryKey,
  updateSettings,
  useReadSettings,
} from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { useVariationName } from "@/features/products/variations";
import { useChangesSettings } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { errorMessage } from "@/lib/errors";
import { useSeesCosts } from "@/lib/permissions";
import { useSession } from "@/session";
import { Field } from "@/ui/field";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";

/** The choice standing for none: what holds above it holds. */
const INHERIT = "inherit";

const CHOICES = Object.values(AutoManufacture);

const chosen = (value: string): AutoManufacture | null =>
  CHOICES.find((choice) => choice === value) ?? null;

/** Whether a store order makes what it sells: a product's own choice, and
 *  each of its variations', on its page; or, with no product, the
 *  organization's in Settings. */
export function AutoManufactureField({ product }: { product?: ProductDetail }) {
  return product === undefined ? <OrganizationChoice /> : <ProductChoice product={product} />;
}

/** Where a choice of none takes its choice from, and what that is. */
interface Inherits {
  from: "settings" | "product";
  choice: AutoManufacture;
}

/** A choice of auto-manufacture: one of its three, or, where something holds
 *  above it, none, which takes that. */
function Choice({
  label,
  value,
  inherits,
  disabled,
  onChange,
}: {
  label: string;
  value: AutoManufacture | null;
  inherits?: Inherits;
  disabled: boolean;
  onChange: (value: AutoManufacture | null) => void;
}) {
  const { t } = useTranslation("sync");
  return (
    <Field label={label}>
      <Select
        value={value ?? INHERIT}
        disabled={disabled}
        onValueChange={(next) => onChange(chosen(next))}
      >
        <SelectTrigger className="w-72">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {inherits && (
            <SelectItem value={INHERIT}>
              {t(`autoManufacture.inherit.${inherits.from}`, {
                choice: t(`autoManufacture.choices.${inherits.choice}`),
              })}
            </SelectItem>
          )}
          {CHOICES.map((choice) => (
            <SelectItem key={choice} value={choice}>
              {t(`autoManufacture.choices.${choice}`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </Field>
  );
}

function OrganizationChoice() {
  const { t } = useTranslation("sync");
  const queryClient = useQueryClient();
  const current = useReadSettings().data?.auto_manufacture;
  const changes = useChangesSettings();
  const save = useMutation({
    mutationFn: (value: AutoManufacture) => updateSettings({ auto_manufacture: value }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: getReadSettingsQueryKey() });
      toast.success(t("autoManufacture.saved"));
    },
    onError: (error) => toast.error(errorMessage(error, t, "autoManufacture.saveFailed")),
  });
  return (
    <SettingsSection id="auto-manufacture" title={t("autoManufacture.title")}>
      <p className="text-sm text-muted-foreground">{t("autoManufacture.description")}</p>
      {current && (
        <Choice
          label={t("autoManufacture.storeOrders")}
          value={current}
          disabled={!changes || save.isPending}
          onChange={(value) => value && save.mutate(value)}
        />
      )}
    </SettingsSection>
  );
}

function ProductChoice({ product }: { product: ProductDetail }) {
  const { t } = useTranslation("sync");
  const queryClient = useQueryClient();
  const { read_only } = useSession();
  const seesCosts = useSeesCosts();
  const variationName = useVariationName();
  const organization = useReadSettings().data?.auto_manufacture;
  const saved = async (detail: ProductDetail) => {
    queryClient.setQueryData(getReadProductQueryKey(detail.id), detail);
    await queryClient.invalidateQueries({ queryKey: getListProductsQueryKey() });
    toast.success(t("autoManufacture.saved"));
  };
  const failed = (error: unknown) =>
    toast.error(errorMessage(error, t, "autoManufacture.saveFailed"));
  const saveProduct = useMutation({
    mutationFn: (value: AutoManufacture | null) =>
      updateProduct(product.id, {
        name: product.name,
        status: product.status,
        category_id: product.category_id,
        low_stock_limit: product.low_stock_limit,
        ...(seesCosts ? { default_price: product.default_price } : {}),
        auto_manufacture: value,
      }),
    onSuccess: saved,
    onError: failed,
  });
  const saveVariation = useMutation({
    mutationFn: ({
      variation,
      value,
    }: {
      variation: ProductVariationRead;
      value: AutoManufacture | null;
    }) =>
      updateProductVariation(product.id, variation.id, {
        attributes: variation.attributes,
        sku: variation.sku,
        ...(seesCosts ? { price: variation.price } : {}),
        auto_manufacture: value,
      }),
    onSuccess: saved,
    onError: failed,
  });
  if (organization === undefined) return null;
  const pending = saveProduct.isPending || saveVariation.isPending;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">{t("autoManufacture.description")}</p>
      <Choice
        label={t("autoManufacture.storeOrders")}
        value={product.auto_manufacture}
        inherits={{ from: "settings", choice: organization }}
        disabled={read_only || pending}
        onChange={(value) => saveProduct.mutate(value)}
      />
      {product.variations.length > 1 &&
        product.variations.map((variation) => (
          <Choice
            key={variation.id}
            label={variationName(variation)}
            value={variation.auto_manufacture}
            inherits={{ from: "product", choice: product.auto_manufacture ?? organization }}
            disabled={read_only || pending}
            onChange={(value) => saveVariation.mutate({ variation, value })}
          />
        ))}
    </div>
  );
}
