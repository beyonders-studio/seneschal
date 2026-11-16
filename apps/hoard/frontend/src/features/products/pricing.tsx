import { useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  PointPriceRead,
  PriceCost,
  ProductDetail,
  ProductVariationRead,
  SettingsPricePointRead,
} from "@/api/generated/hoard.schemas";
import { getReadProductQueryKey, useSetVariationPrice } from "@/api/generated/products/products";
import { useListSettingsPricePoints } from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { orNull, useShare } from "@/features/items/format";
import { useVariationName } from "@/features/products/variations";
import { errorMessage } from "@/lib/errors";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { useSession } from "@/session";
import { Badge } from "@/ui/badge";
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
import { cn } from "@/ui/lib/utils";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** A variation's price being set at a price point. */
type Pricing = { variation: ProductVariationRead; point: SettingsPricePointRead };

/**
 * What each of a product's variations costs and sells for, to a member who
 * sees costs: its cost by its parts, and at each price point its price, typed
 * in or suggested from the cost by the price point's markup, the listing fee
 * and commission it pays, and the margin left, marked when it is a loss.
 * Without a cost, the suggested price and margin are unknown.
 */
export function PricingPanel({ product }: { product: ProductDetail }) {
  const { t } = useTranslation("catalog");
  const variationName = useVariationName();
  const seesCosts = useSeesCosts();
  const points = useListSettingsPricePoints({ query: { enabled: seesCosts } }).data;
  const [pricing, setPricing] = useState<Pricing | null>(null);
  if (!seesCosts || !points) return null;
  const named = product.variations.length > 1;
  return (
    <div className="flex flex-col gap-4">
      {product.variations.map((variation) => (
        <div key={variation.id} className="flex flex-col gap-2">
          {named && <h4 className="text-sm font-medium">{variationName(variation)}</h4>}
          <CostParts product={product} variation={variation} />
          {points.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("products.pricing.noPricePoints")}</p>
          ) : (
            <PointPrices
              variation={variation}
              points={points}
              onSet={(point) => setPricing({ variation, point })}
            />
          )}
        </div>
      ))}
      <PriceDialog product={product} pricing={pricing} onClose={() => setPricing(null)} />
    </div>
  );
}

/** What one of a variation costs: its goods (its materials, for one made; its
 *  contents', for a bundle), its labour and overhead, and in all. */
function CostParts({
  product,
  variation,
}: {
  product: ProductDetail;
  variation: ProductVariationRead;
}) {
  const { t } = useTranslation("catalog");
  const money = useMoney();
  const cost: PriceCost | null | undefined = variation.pricing?.cost;
  if (!cost) return <p className="text-sm text-muted-foreground">{t("products.pricing.noCost")}</p>;
  const goods = product.bundle
    ? t("products.pricing.contents")
    : variation.potential !== null
      ? t("products.pricing.materials")
      : t("items.averageCost");
  const parts: [string, string | null][] = [
    [goods, cost.goods],
    [t("products.pricing.labour"), cost.labour],
    [t("products.pricing.overhead"), cost.overhead],
  ];
  return (
    <dl className="grid grid-cols-fill-32/4 gap-3 text-sm">
      {parts
        .filter((part): part is [string, string] => part[1] !== null)
        .map(([label, amount]) => (
          <div key={label} className="flex flex-col gap-0.5">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="inline-flex items-center gap-2 tabular-nums">
              {money(amount, { exact: true })}
              {label === goods && cost.last_known && (
                <Badge variant="outline">{t("products.pricing.lastKnown")}</Badge>
              )}
            </dd>
          </div>
        ))}
      <div className="flex flex-col gap-0.5">
        <dt className="text-xs text-muted-foreground">{t("products.pricing.total")}</dt>
        <dd className="font-medium tabular-nums">{money(cost.total, { exact: true })}</dd>
      </div>
    </dl>
  );
}

/** A variation at each price point, offering to set its price there. */
function PointPrices({
  variation,
  points,
  onSet,
}: {
  variation: ProductVariationRead;
  points: SettingsPricePointRead[];
  onSet: (point: SettingsPricePointRead) => void;
}) {
  const { t } = useTranslation("catalog");
  const { read_only } = useSession();
  const money = useMoney();
  const share = useShare();
  const priced = new Map(
    (variation.pricing?.points ?? []).map((at): [number, PointPriceRead] => [at.price_point_id, at])
  );
  const unknown = <span className="text-muted-foreground">{t("products.pricing.unknown")}</span>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("products.pricing.pricePoint")}</TableHead>
          <TableHead className="text-right">{t("hoard:common.price")}</TableHead>
          <TableHead className="text-right">{t("products.pricing.suggested")}</TableHead>
          <TableHead className="text-right">{t("products.pricing.fee")}</TableHead>
          <TableHead className="text-right">{t("products.pricing.commission")}</TableHead>
          <TableHead className="text-right">{t("products.pricing.margin")}</TableHead>
          {!read_only && <TableHead className="w-12" />}
        </TableRow>
      </TableHeader>
      <TableBody>
        {points.map((point) => {
          const at = priced.get(point.id);
          const loss = at?.margin != null && Number(at.margin) < 0;
          return (
            <TableRow key={point.id}>
              <TableCell>{point.name}</TableCell>
              <TableCell className="text-right tabular-nums">
                {at?.price ? money(at.price) : unknown}
                {at?.price && at.typed === null && (
                  <span className="ml-2 text-xs text-muted-foreground">
                    {t("products.pricing.usesSuggested")}
                  </span>
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {at?.suggested ? money(at.suggested) : unknown}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {at && money(at.fee, { exact: true })}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {at?.commission ? money(at.commission, { exact: true }) : unknown}
              </TableCell>
              <TableCell className={cn("text-right tabular-nums", loss && "text-destructive")}>
                {at?.margin ? (
                  <span className="inline-flex items-center gap-2">
                    {loss && <Badge variant="destructive">{t("products.pricing.loss")}</Badge>}
                    {t("products.pricing.marginIs", {
                      margin: money(at.margin, { exact: true }),
                      share: at.margin_share ? share(at.margin_share) : "",
                    })}
                  </span>
                ) : (
                  unknown
                )}
              </TableCell>
              {!read_only && (
                <TableCell>
                  <Button variant="ghost" size="sm" onClick={() => onSet(point)}>
                    {t("products.pricing.setPrice")}
                  </Button>
                </TableCell>
              )}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

/** Sets a variation's price at a price point; left blank, it sells there at
 *  its suggested price. */
function PriceDialog({
  product,
  pricing,
  onClose,
}: {
  product: ProductDetail;
  pricing: Pricing | null;
  onClose: () => void;
}) {
  const { t } = useTranslation("catalog");
  const variationName = useVariationName();
  return (
    <Dialog open={pricing !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {pricing &&
              t("products.pricing.priceTitle", {
                name:
                  product.variations.length > 1
                    ? `${product.name} (${variationName(pricing.variation)})`
                    : product.name,
                point: pricing.point.name,
              })}
          </DialogTitle>
        </DialogHeader>
        {pricing && <PriceForm product={product} pricing={pricing} onSaved={onClose} />}
      </DialogContent>
    </Dialog>
  );
}

function PriceForm({
  product,
  pricing: { variation, point },
  onSaved,
}: {
  product: ProductDetail;
  pricing: Pricing;
  onSaved: () => void;
}) {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const typed = variation.pricing?.points.find((at) => at.price_point_id === point.id)?.typed;
  const [price, setPrice] = useState(typed ?? "");
  const save = useSetVariationPrice();
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const saved = await save.mutateAsync({
        productId: product.id,
        variationId: variation.id,
        pricePointId: point.id,
        data: { price: orNull(price) },
      });
      queryClient.setQueryData(getReadProductQueryKey(product.id), saved);
      onSaved();
    } catch (error) {
      toast.error(errorMessage(error, t, "products.pricing.saveFailed"));
    }
  };
  return (
    <form onSubmit={submit} className="contents">
      <DialogBody>
        <Field label={t("hoard:common.price")} hint={t("products.pricing.priceHint")}>
          <Input
            inputMode="decimal"
            value={price}
            onChange={(event) => setPrice(event.target.value)}
          />
        </Field>
      </DialogBody>
      <DialogFooter>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? t("hoard:common.saving") : t("hoard:common.save")}
        </Button>
      </DialogFooter>
    </form>
  );
}
