import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  ProductDetail,
  ProductVariationRead,
  ProductVariationWrite,
} from "@/api/generated/hoard.schemas";
import {
  createProductVariation,
  getReadProductQueryKey,
  updateProductVariation,
} from "@/api/generated/products/products";
import { orNull } from "@/features/items/format";
import {
  changedInStore,
  storeVersions,
  useListedIn,
  useRefreshStoreProducts,
} from "@/features/products/storeVersions";
import { errorMessage } from "@/lib/errors";
import { useSeesCosts } from "@/lib/permissions";
import { Button } from "@/ui/button";
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

/**
 * Adding a variation to `product`, or editing `variation`, in a dialog.
 * Closing it drops what was typed. Its price is a field only for a member who
 * sees costs. A new SKU or attributes of a variation a synced store lists are
 * written to the store first, as the product's are (`ProductDialog`).
 */
export function VariationDialog({
  product,
  variation,
  open,
  onOpenChange,
}: {
  product: ProductDetail;
  variation?: ProductVariationRead;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        {open && (
          <VariationForm
            product={product}
            variation={variation}
            onSaved={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

function VariationForm({
  product,
  variation,
  onSaved,
}: {
  product: ProductDetail;
  variation?: ProductVariationRead;
  onSaved: () => void;
}) {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const seesCosts = useSeesCosts();
  const [attributes, setAttributes] = useState(() =>
    Object.entries(variation?.attributes ?? {}).concat(variation ? [] : [["", ""]])
  );
  const [sku, setSku] = useState(variation?.sku ?? "");
  const [price, setPrice] = useState(variation?.price ?? "");
  const listedIn = useListedIn();
  const refresh = useRefreshStoreProducts();
  const save = useMutation({
    mutationFn: (body: ProductVariationWrite) =>
      variation
        ? updateProductVariation(product.id, variation.id, body)
        : createProductVariation(product.id, body),
    onSuccess: async (saved) => {
      queryClient.setQueryData(getReadProductQueryKey(product.id), saved);
      await refresh(product);
      onSaved();
    },
    onError: (error) => {
      if (changedInStore(error)) void refresh(product);
    },
  });

  const setAttribute = (index: number, part: 0 | 1, value: string) =>
    setAttributes((rows) =>
      rows.map((row, at) => (at === index ? (part === 0 ? [value, row[1]] : [row[0], value]) : row))
    );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({
      attributes: Object.fromEntries(
        attributes
          .map(([name, value]) => [name.trim(), value.trim()])
          .filter(([name, value]) => name && value)
      ),
      sku: orNull(sku),
      ...(seesCosts ? { price: orNull(price) } : {}),
      store_versions: storeVersions(product),
    });
  };

  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>
          {variation ? t("products.editVariation") : t("products.addVariation")}
        </DialogTitle>
      </DialogHeader>
      <DialogBody className="flex flex-col gap-4">
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-2 text-sm font-medium">{t("products.attributes")}</legend>
          {attributes.map(([name, value], index) => (
            // Rows are edited in place and never reordered.
            // biome-ignore lint/suspicious/noArrayIndexKey: see above
            <div key={index} className="flex items-center gap-2">
              <Input
                aria-label={t("products.attribute")}
                placeholder={t("products.attributeHint")}
                value={name}
                onChange={(event) => setAttribute(index, 0, event.target.value)}
              />
              <Input
                aria-label={t("products.attributeValue")}
                placeholder={t("products.attributeValueHint")}
                value={value}
                onChange={(event) => setAttribute(index, 1, event.target.value)}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={t("products.removeAttribute")}
                onClick={() => setAttributes((rows) => rows.filter((_, at) => at !== index))}
              >
                <X aria-hidden />
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="self-start"
            onClick={() => setAttributes((rows) => [...rows, ["", ""]])}
          >
            <Plus aria-hidden />
            {t("products.addAttribute")}
          </Button>
        </fieldset>
        <div className="grid grid-cols-pair gap-4">
          <Field label={t("hoard:common.sku")}>
            <Input value={sku} onChange={(event) => setSku(event.target.value)} />
          </Field>
          {seesCosts && (
            <Field label={t("hoard:common.price")} hint={t("products.priceHint")}>
              <Input
                inputMode="decimal"
                value={price}
                onChange={(event) => setPrice(event.target.value)}
              />
            </Field>
          )}
        </div>
        {save.isError && (
          <p role="alert" className="text-sm text-destructive">
            {changedInStore(save.error)
              ? t("products.changedInStore", { store: listedIn(product) })
              : errorMessage(save.error, t, "products.saveFailed")}
          </p>
        )}
      </DialogBody>
      <DialogFooter
        submitLabel={t("hoard:common.save")}
        pending={save.isPending}
        pendingLabel={t("hoard:common.saving")}
      />
    </form>
  );
}
