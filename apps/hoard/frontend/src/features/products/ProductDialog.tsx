import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type ProductDetail,
  ProductStatus,
  type ProductWrite,
} from "@/api/generated/hoard.schemas";
import {
  createProduct,
  getListProductsQueryKey,
  getReadProductQueryKey,
  updateProduct,
} from "@/api/generated/products/products";
import { orNull } from "@/features/items/format";
import { IdSelect } from "@/features/items/IdSelect";
import { useSettingsNames } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { useSeesCosts } from "@/lib/permissions";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/ui/dialog";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";

/**
 * Adding a product, or editing `product`, in a dialog. Closing it drops what
 * was typed. Its default price is a field only for a member who sees costs.
 */
export function ProductDialog({
  product,
  open,
  onOpenChange,
  onSaved,
}: {
  product?: ProductDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved?: (product: ProductDetail) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {open && (
          <ProductForm
            product={product}
            onSaved={(saved) => {
              onOpenChange(false);
              onSaved?.(saved);
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function ProductForm({
  product,
  onSaved,
}: {
  product?: ProductDetail;
  onSaved: (product: ProductDetail) => void;
}) {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const seesCosts = useSeesCosts();
  const { categoriesOf } = useSettingsNames();
  const [name, setName] = useState(product?.name ?? "");
  const [status, setStatus] = useState<ProductStatus>(product?.status ?? ProductStatus.active);
  const [categoryId, setCategoryId] = useState(product?.category_id ?? null);
  const [lowStockLimit, setLowStockLimit] = useState(product?.low_stock_limit ?? "");
  const [defaultPrice, setDefaultPrice] = useState(product?.default_price ?? "");
  const save = useMutation({
    mutationFn: (body: ProductWrite) =>
      product ? updateProduct(product.id, body) : createProduct(body),
    onSuccess: async (saved) => {
      queryClient.setQueryData(getReadProductQueryKey(saved.id), saved);
      await queryClient.invalidateQueries({ queryKey: getListProductsQueryKey() });
      onSaved(saved);
    },
  });

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({
      name: name.trim(),
      status,
      category_id: categoryId,
      low_stock_limit: orNull(lowStockLimit),
      ...(seesCosts ? { default_price: orNull(defaultPrice) } : {}),
    });
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{product ? t("products.edit") : t("products.add")}</DialogTitle>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-4">
        <Field label={t("hoard:common.name")}>
          <Input value={name} onChange={(event) => setName(event.target.value)} required />
        </Field>
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("hoard:common.category")}>
            <IdSelect
              value={categoryId}
              options={categoriesOf("product")}
              onChange={setCategoryId}
              none={t("hoard:common.none")}
            />
          </Field>
          <Field label={t("products.status")}>
            <Select value={status} onValueChange={(value) => setStatus(value as ProductStatus)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.values(ProductStatus).map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(`products.statuses.${value}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t("hoard:common.lowStockLimit")}>
            <Input
              inputMode="decimal"
              value={lowStockLimit}
              onChange={(event) => setLowStockLimit(event.target.value)}
            />
          </Field>
          {seesCosts && (
            <Field label={t("products.defaultPrice")}>
              <Input
                inputMode="decimal"
                value={defaultPrice}
                onChange={(event) => setDefaultPrice(event.target.value)}
              />
            </Field>
          )}
        </div>
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(save.error, t, "products.saveFailed")}
          </p>
        )}
      </DialogBody>
      <DialogFooter
        submitLabel={t("hoard:common.save")}
        pending={save.isPending}
        pendingLabel={t("hoard:common.saving")}
        disabled={!name.trim()}
      />
    </form>
  );
}
