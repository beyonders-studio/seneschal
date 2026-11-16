import { useTranslation } from "react-i18next";

import type { ProductDetail, ProductVariationRead } from "@/api/generated/hoard.schemas";
import { useShare } from "@/features/items/format";
import { useVariationName } from "@/features/products/variations";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { Badge } from "@/ui/badge";
import { cn } from "@/ui/lib/utils";

/**
 * What each of a product's variations costs, to a member who sees costs: its
 * cost by its parts, its price (the store's, where a synced store lists it)
 * and the margin left at that price, marked when it is a loss. Without a
 * cost, the margin is unknown.
 */
export function PricingPanel({ product }: { product: ProductDetail }) {
  const variationName = useVariationName();
  if (!useSeesCosts()) return null;
  const named = product.variations.length > 1;
  return (
    <div className="flex flex-col gap-4">
      {product.variations.map((variation) => (
        <div key={variation.id} className="flex flex-col gap-2">
          {named && <h4 className="text-sm font-medium">{variationName(variation)}</h4>}
          <VariationCost product={product} variation={variation} />
        </div>
      ))}
    </div>
  );
}

/** What one of a variation costs: its goods (its materials, for one made; its
 *  contents', for a bundle), its labour and overhead, and in all; then its
 *  price and the margin left there. */
function VariationCost({
  product,
  variation,
}: {
  product: ProductDetail;
  variation: ProductVariationRead;
}) {
  const { t } = useTranslation("catalog");
  const money = useMoney();
  const share = useShare();
  const pricing = variation.pricing;
  if (!pricing) return null;
  const { cost, price, margin, margin_share } = pricing;
  const goods = product.bundle
    ? t("products.pricing.contents")
    : variation.potential !== null
      ? t("products.pricing.materials")
      : t("items.averageCost");
  const parts: [string, string | null][] = cost
    ? [
        [goods, cost.goods],
        [t("products.pricing.labour"), cost.labour],
        [t("products.pricing.overhead"), cost.overhead],
      ]
    : [];
  const loss = margin !== null && Number(margin) < 0;
  const unknown = <span className="text-muted-foreground">{t("products.pricing.unknown")}</span>;
  return (
    <div className="flex flex-col gap-2">
      {!cost && <p className="text-sm text-muted-foreground">{t("products.pricing.noCost")}</p>}
      <dl className="grid grid-cols-fill-32/6 gap-3 text-sm">
        {parts
          .filter((part): part is [string, string] => part[1] !== null)
          .map(([label, amount]) => (
            <div key={label} className="flex flex-col gap-0.5">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="inline-flex items-center gap-2 tabular-nums">
                {money(amount, { exact: true })}
                {label === goods && cost?.last_known && (
                  <Badge variant="outline">{t("products.pricing.lastKnown")}</Badge>
                )}
              </dd>
            </div>
          ))}
        {cost && (
          <div className="flex flex-col gap-0.5">
            <dt className="text-xs text-muted-foreground">{t("products.pricing.total")}</dt>
            <dd className="font-medium tabular-nums">{money(cost.total, { exact: true })}</dd>
          </div>
        )}
        <div className="flex flex-col gap-0.5">
          <dt className="text-xs text-muted-foreground">
            {pricing.store_price ? t("products.pricing.storePrice") : t("products.pricing.price")}
          </dt>
          <dd className="tabular-nums">{price === null ? unknown : money(price)}</dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt className="text-xs text-muted-foreground">{t("products.pricing.margin")}</dt>
          <dd
            className={cn(
              "inline-flex items-center gap-2 tabular-nums",
              loss && "text-destructive"
            )}
          >
            {margin === null
              ? unknown
              : t("products.pricing.marginIs", {
                  margin: money(margin, { exact: true }),
                  share: margin_share === null ? "" : share(margin_share),
                })}
            {loss && <Badge variant="destructive">{t("products.pricing.loss")}</Badge>}
          </dd>
        </div>
      </dl>
    </div>
  );
}
