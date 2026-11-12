import { useQueryClient } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Store } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import type { ListingRead } from "@/api/generated/hoard.schemas";
import {
  getListListingsQueryKey,
  useLinkListing,
  useListListings,
} from "@/api/generated/listings/listings";
import { getListStoresQueryKey } from "@/api/generated/stores/stores";
import { toast } from "@/components/Toaster";
import { ItemField } from "@/features/adjustments/fields";
import { errorMessage } from "@/lib/errors";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/ui/dialog";
import { Input } from "@/ui/input";
import { PaginationBar } from "@/ui/pagination-bar";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** How many listings a page lists. */
const PAGE_SIZE = 50;

/** Which listings the page shows: every one, or only those linked or not. */
const SHOWN = { all: undefined, unlinked: false, linkedOnly: true } as const;
type Shown = keyof typeof SHOWN;
const isShown = (value: string): value is Shown => value in SHOWN;

/** The stores' listings, each with the item it sells, under Orders. */
export function ListingsPage() {
  const { t } = useTranslation("stores");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [shown, setShown] = useState<Shown>("all");
  const [linking, setLinking] = useState<ListingRead | null>(null);
  const listings = useListListings({
    q: search || undefined,
    linked: SHOWN[shown],
    offset: (page - 1) * PAGE_SIZE,
    limit: PAGE_SIZE,
  });
  const filtered = search !== "" || shown !== "all";

  let body: ReactNode;
  if (listings.isError) body = <StatusMessage onRetry={() => void listings.refetch()} />;
  else if (!listings.data)
    body = <p className="text-muted-foreground">{t("hoard:common.loading")}</p>;
  else if (listings.data.total === 0 && !filtered)
    body = (
      <StatusMessage
        icon={<Store />}
        title={t("listings.emptyTitle")}
        description={t("listings.emptyBody")}
      />
    );
  else
    body = (
      <>
        <ListingsTable listings={listings.data.items} onLink={setLinking} />
        {listings.data.total > PAGE_SIZE && (
          <PaginationBar
            page={page}
            pageSize={PAGE_SIZE}
            totalCount={listings.data.total}
            hasNext={page * PAGE_SIZE < listings.data.total}
            onPageChange={setPage}
          />
        )}
      </>
    );

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type="search"
          value={search}
          aria-label={t("hoard:common.search")}
          placeholder={t("hoard:common.search")}
          className="max-w-xs"
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(1);
          }}
        />
        <Select
          value={shown}
          onValueChange={(value) => {
            if (isShown(value)) setShown(value);
            setPage(1);
          }}
        >
          <SelectTrigger aria-label={t("listings.show")} className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {(Object.keys(SHOWN) as Shown[]).map((each) => (
              <SelectItem key={each} value={each}>
                {t(`listings.${each}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {body}
      <Dialog open={linking !== null} onOpenChange={(open) => !open && setLinking(null)}>
        <DialogContent>
          {linking && <LinkForm listing={linking} onDone={() => setLinking(null)} />}
        </DialogContent>
      </Dialog>
    </section>
  );
}

function ListingsTable({
  listings,
  onLink,
}: {
  listings: ListingRead[];
  onLink: (listing: ListingRead) => void;
}) {
  const { t } = useTranslation("stores");
  const { read_only } = useSession();
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("listings.title")}</TableHead>
          <TableHead>{t("hoard:common.sku")}</TableHead>
          <TableHead>{t("listings.store")}</TableHead>
          <TableHead>{t("listings.sells")}</TableHead>
          <TableHead>{t("listings.matchedBy")}</TableHead>
          <TableHead />
        </TableRow>
      </TableHeader>
      <TableBody>
        {listings.map((listing) => (
          <TableRow key={listing.id}>
            <TableCell className="font-medium">{listing.title ?? listing.product_id}</TableCell>
            <TableCell>{listing.sku}</TableCell>
            <TableCell>{t(`channels.${listing.channel}`)}</TableCell>
            <TableCell>
              <ItemName listing={listing} />
            </TableCell>
            <TableCell>
              {listing.matched_by ? t(`listings.matched.${listing.matched_by}`) : null}
            </TableCell>
            <TableCell className="text-right">
              <Button
                variant="outline"
                size="sm"
                disabled={read_only}
                aria-label={t("listings.linkTitle", { title: listing.title ?? listing.product_id })}
                onClick={() => onLink(listing)}
              >
                {t("listings.link")}
              </Button>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** The item a listing sells, opening its page. */
function ItemName({ listing }: { listing: ListingRead }) {
  const { t } = useTranslation("stores");
  const style = "underline-offset-4 hover:underline";
  if (listing.item_id === null || !listing.item_name)
    return <span className="text-muted-foreground">{t("listings.sellsNothing")}</span>;
  if (listing.item_product_id !== null)
    return (
      <Link
        to="/stock/products/$productId"
        params={{ productId: String(listing.item_product_id) }}
        className={style}
      >
        {listing.item_name}
      </Link>
    );
  return (
    <Link
      to="/stock/materials/$materialId"
      params={{ materialId: String(listing.item_id) }}
      className={style}
    >
      {listing.item_name}
    </Link>
  );
}

/** Choosing the item a listing sells, or that it sells nothing. */
function LinkForm({ listing, onDone }: { listing: ListingRead; onDone: () => void }) {
  const { t } = useTranslation("stores");
  const queryClient = useQueryClient();
  const [item, setItem] = useState<number | null>(listing.item_id);
  const link = useLinkListing({
    mutation: {
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: getListListingsQueryKey() });
        await queryClient.invalidateQueries({ queryKey: getListStoresQueryKey() });
        toast.success(t("listings.saved"));
        onDone();
      },
      onError: (error) => toast.error(errorMessage(error, t, "listings.saveFailed")),
    },
  });
  // Not a form: the item field's own buttons would submit one.
  return (
    <>
      <DialogHeader>
        <DialogTitle>
          {t("listings.linkTitle", { title: listing.title ?? listing.product_id })}
        </DialogTitle>
      </DialogHeader>
      <DialogBody className="space-y-4">
        <ItemField
          label={t("listings.item")}
          value={item}
          chosen={listing.item_name}
          kinds={["variation", "material"]}
          clearLabel={t("listings.clear")}
          onChange={setItem}
        />
      </DialogBody>
      <DialogFooter>
        <Button
          pending={link.isPending}
          onClick={() => link.mutate({ listingId: listing.id, data: { item_id: item } })}
        >
          {t("hoard:common.save")}
        </Button>
      </DialogFooter>
    </>
  );
}
