import { useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  ProductDetail,
  StoreImageWrite,
  StoreProductRead,
  StoreRead,
} from "@/api/generated/hoard.schemas";
import { getListListingsQueryKey, useListListings } from "@/api/generated/listings/listings";
import {
  getListStoresQueryKey,
  getReadStoreProductQueryKey,
  useCreateStoreProduct,
  useListStores,
  useReadStoreProduct,
  useSaveStoreProduct,
} from "@/api/generated/stores/stores";
import { toast } from "@/components/Toaster";
import { useStoreName } from "@/features/stores/words";
import { errorMessage } from "@/lib/errors";
import { useSeesCosts } from "@/lib/permissions";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";
import { Textarea } from "@/ui/textarea";

/** The most listings of one product the section reads. */
const LISTINGS = 200;

/** A product in each connected store, on its page: its listings, and the
 *  store's own title, description, images and prices, edited live. */
export function StoreSection({ product }: { product: ProductDetail }) {
  const { t } = useTranslation("stores");
  const stores = useListStores().data ?? [];
  const listings = useListListings({ product_id: product.id, limit: LISTINGS }).data?.items;
  if (!listings) return <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;

  // Each store product the product's variations are listed under, once.
  const listed = [
    ...new Map(
      listings.map((listing) => [
        `${listing.store_id}:${listing.product_id}`,
        { storeId: listing.store_id, productId: listing.product_id },
      ])
    ).values(),
  ];
  const unlisted = stores.filter(
    (store) => store.connected && !listings.some((listing) => listing.store_id === store.id)
  );

  return (
    <div className="flex flex-col gap-4">
      {listed.length === 0 && <p className="text-sm text-muted-foreground">{t("product.empty")}</p>}
      {listed.map(({ storeId, productId }) => {
        const store = stores.find((each) => each.id === storeId);
        return (
          store && (
            <StoreProduct key={`${storeId}:${productId}`} store={store} productId={productId} />
          )
        );
      })}
      {unlisted.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {unlisted.map((store) => (
            <AddToStore key={store.id} store={store} productId={product.id} />
          ))}
        </div>
      )}
    </div>
  );
}

function AddToStore({ store, productId }: { store: StoreRead; productId: number }) {
  const { t } = useTranslation("stores");
  const name = useStoreName();
  const { read_only } = useSession();
  const queryClient = useQueryClient();
  const add = useCreateStoreProduct({
    mutation: {
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: getListListingsQueryKey() });
        await queryClient.invalidateQueries({ queryKey: getListStoresQueryKey() });
        toast.success(t("product.added"));
      },
      onError: (error) => toast.error(errorMessage(error, t, "product.addFailed")),
    },
  });
  return (
    <Button
      variant="outline"
      disabled={read_only}
      pending={add.isPending}
      onClick={() => add.mutate({ storeId: store.id, data: { product_id: productId } })}
    >
      <Plus aria-hidden />
      {t("product.addTo", { store: name(store) })}
    </Button>
  );
}

function StoreProduct({ store, productId }: { store: StoreRead; productId: string }) {
  const { t } = useTranslation("stores");
  const name = useStoreName();
  const read = useReadStoreProduct(store.id, productId);
  return (
    <section aria-label={name(store)} className="flex flex-col gap-3 rounded-md border p-4">
      <div>
        <h3 className="font-medium">{name(store)}</h3>
        <p className="text-xs text-muted-foreground">{t("product.live")}</p>
      </div>
      {read.isError && (
        <StatusMessage
          title={errorMessage(read.error, t, "product.loadFailed")}
          onRetry={() => void read.refetch()}
        />
      )}
      {read.data && <StoreProductForm key={read.dataUpdatedAt} held={read.data} />}
    </section>
  );
}

/** A variant's edits as typed: its SKU and price. */
interface VariantDraft {
  sku: string;
  price: string;
}

function StoreProductForm({ held }: { held: StoreProductRead }) {
  const { t } = useTranslation("stores");
  const seesCosts = useSeesCosts();
  const { read_only } = useSession();
  const queryClient = useQueryClient();
  const [title, setTitle] = useState(held.title);
  const [description, setDescription] = useState(held.description ?? "");
  const [status, setStatus] = useState(held.status ?? "");
  const [variants, setVariants] = useState<Record<string, VariantDraft>>(() =>
    Object.fromEntries(
      held.variants.map((variant) => [
        variant.id ?? "",
        { sku: variant.sku ?? "", price: variant.price ?? "" },
      ])
    )
  );
  const [images, setImages] = useState<StoreImageWrite[]>(held.images);
  const save = useSaveStoreProduct({
    mutation: {
      onSuccess: (saved) => {
        queryClient.setQueryData(getReadStoreProductQueryKey(held.store_id, held.id), saved);
        void queryClient.invalidateQueries({ queryKey: getListListingsQueryKey() });
        toast.success(t("product.saved"));
      },
      onError: (error) => toast.error(errorMessage(error, t, "product.saveFailed")),
    },
  });
  const setVariant = (id: string, change: Partial<VariantDraft>) =>
    setVariants((drafts) => ({ ...drafts, [id]: { ...drafts[id], ...change } }));
  const setImage = (index: number, change: Partial<StoreImageWrite>) =>
    setImages((now) => now.map((image, at) => (at === index ? { ...image, ...change } : image)));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate({
      storeId: held.store_id,
      productId: held.id,
      data: {
        title: title.trim(),
        description: description || null,
        status: status.trim() || null,
        variants: Object.entries(variants).map(([id, draft]) => ({
          id,
          sku: draft.sku.trim() || null,
          price: seesCosts ? draft.price.trim() || null : null,
        })),
        images: images.filter((image) => image.url.trim() !== ""),
        published_on: held.published_on,
      },
    });
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <div className="grid grid-cols-pair gap-4">
        <Field label={t("product.storeTitle")}>
          <Input
            required
            maxLength={200}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </Field>
        <Field label={t("product.status")} hint={t("product.statusHint")}>
          <Input value={status} onChange={(event) => setStatus(event.target.value)} />
        </Field>
      </div>
      <Field label={t("product.description")} hint={t("product.descriptionHint")}>
        <Textarea value={description} onChange={(event) => setDescription(event.target.value)} />
      </Field>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>{t("product.variant")}</TableHead>
            <TableHead>{t("hoard:common.sku")}</TableHead>
            {seesCosts && <TableHead>{t("hoard:common.price")}</TableHead>}
          </TableRow>
        </TableHeader>
        <TableBody>
          {held.variants.map((variant) => {
            const id = variant.id ?? "";
            return (
              <TableRow key={id}>
                <TableCell>{variant.title}</TableCell>
                <TableCell>
                  <Input
                    aria-label={`${t("hoard:common.sku")} ${variant.title}`}
                    value={variants[id]?.sku ?? ""}
                    onChange={(event) => setVariant(id, { sku: event.target.value })}
                  />
                </TableCell>
                {seesCosts && (
                  <TableCell>
                    <Input
                      type="number"
                      min={0}
                      step="any"
                      aria-label={`${t("hoard:common.price")} ${variant.title}`}
                      value={variants[id]?.price ?? ""}
                      onChange={(event) => setVariant(id, { price: event.target.value })}
                    />
                  </TableCell>
                )}
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-2 text-sm font-medium">{t("product.images")}</legend>
        {images.map((image, index) => (
          <div key={image.id ?? `new-${index}`} className="flex flex-wrap items-center gap-2">
            {image.url && (
              <img src={image.url} alt={image.alt ?? ""} className="size-10 rounded object-cover" />
            )}
            <Input
              aria-label={t("product.imageUrl")}
              value={image.url}
              className="min-w-48 flex-1"
              onChange={(event) => setImage(index, { url: event.target.value })}
            />
            <Input
              aria-label={t("product.imageAlt")}
              placeholder={t("product.imageAlt")}
              value={image.alt ?? ""}
              className="min-w-32 flex-1"
              onChange={(event) => setImage(index, { alt: event.target.value || null })}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={t("product.removeImage")}
              onClick={() => setImages((now) => now.filter((_, at) => at !== index))}
            >
              <Trash2 aria-hidden />
            </Button>
          </div>
        ))}
        <div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setImages((now) => [...now, { url: "", alt: null }])}
          >
            <Plus aria-hidden />
            {t("product.addImage")}
          </Button>
        </div>
      </fieldset>
      <div>
        <Button type="submit" disabled={read_only || !title.trim()} pending={save.isPending}>
          {t("product.save")}
        </Button>
      </div>
    </form>
  );
}
