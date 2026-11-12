import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import type { StoreLocationRead, StoreRead } from "@/api/generated/hoard.schemas";
import { useListSettingsLocations } from "@/api/generated/settings/settings";
import {
  getListStoresQueryKey,
  useListStores,
  useMapStoreLocation,
  useRefreshStore,
} from "@/api/generated/stores/stores";
import { toast } from "@/components/Toaster";
import { IdSelect } from "@/features/items/IdSelect";
import { useChangesSettings } from "@/features/settings/NamedList";
import { useLocationName } from "@/features/settings/names";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { PushLog } from "@/features/stores/PushLog";
import { useStoreName, useStoreReason } from "@/features/stores/words";
import { errorMessage } from "@/lib/errors";
import { Alert, AlertDescription } from "@/ui/alert";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** The community's connected stores: how each connection stands, and where
 *  each store's locations take their stock from. */
export function StoresSection() {
  const { t } = useTranslation("stores");
  const stores = useListStores();
  return (
    <SettingsSection id="stores" title={t("section.title")}>
      <p className="text-sm text-muted-foreground">{t("section.description")}</p>
      {stores.data?.length === 0 && <p className="text-sm">{t("section.empty")}</p>}
      {stores.data?.map((store) => (
        <StoreCard key={store.id} store={store} />
      ))}
    </SettingsSection>
  );
}

function StoreCard({ store }: { store: StoreRead }) {
  const { t } = useTranslation("stores");
  const name = useStoreName();
  const reason = useStoreReason();
  const changes = useChangesSettings();
  const queryClient = useQueryClient();
  const [showingLog, setShowingLog] = useState(false);
  const refresh = useRefreshStore({
    mutation: {
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: getListStoresQueryKey() });
        toast.success(t("section.refreshed"));
      },
      onError: (error) => toast.error(errorMessage(error, t, "section.refreshFailed")),
    },
  });
  const counts = [
    t("section.listings", { count: store.listings, linked: store.linked }),
    store.pushes_queued ? t("section.pushesQueued", { count: store.pushes_queued }) : null,
    store.pushes_failed ? t("section.pushesFailed", { count: store.pushes_failed }) : null,
  ].filter(Boolean);

  return (
    <article aria-label={name(store)} className="flex flex-col gap-3 rounded-md border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-col gap-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-medium">{name(store)}</h3>
            <Badge variant={store.connected ? "secondary" : "outline"}>
              {store.connected ? t("section.connected") : t("section.notConnected")}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            {[t(`channels.${store.channel}`), store.shop_domain].filter(Boolean).join(" · ")}
          </p>
          <p className="text-sm text-muted-foreground">{counts.join(" · ")}</p>
        </div>
        {changes && store.connected && (
          <Button
            variant="outline"
            pending={refresh.isPending}
            onClick={() => refresh.mutate({ storeId: store.id })}
          >
            <RefreshCw aria-hidden />
            {t("section.refresh")}
          </Button>
        )}
      </div>
      {store.last_check_error && (
        <Alert variant="destructive">
          <AlertDescription>{reason(store.last_check_error)}</AlertDescription>
        </Alert>
      )}
      <StoreLocations store={store} changes={changes} />
      <div>
        <Button variant="ghost" size="sm" onClick={() => setShowingLog(!showingLog)}>
          {showingLog ? t("section.hideLog") : t("section.showLog")}
        </Button>
      </div>
      {showingLog && <PushLog storeId={store.id} />}
    </article>
  );
}

function StoreLocations({ store, changes }: { store: StoreRead; changes: boolean }) {
  const { t } = useTranslation("stores");
  const locations = useListSettingsLocations().data ?? [];
  const locationName = useLocationName();
  const queryClient = useQueryClient();
  const map = useMapStoreLocation({
    mutation: {
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: getListStoresQueryKey() });
        toast.success(t("section.mapped"));
      },
      onError: (error) => toast.error(errorMessage(error, t, "section.mapFailed")),
    },
  });
  const options = locations.map((location) => ({ id: location.id, name: locationName(location) }));
  const sellsFrom = (held: StoreLocationRead) =>
    options.find((option) => option.id === held.location_id)?.name ?? t("section.nothing");

  if (store.locations.length === 0)
    return <p className="text-sm text-muted-foreground">{t("section.noLocations")}</p>;
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("section.storeLocation")}</TableHead>
          <TableHead>{t("section.sellsFrom")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {store.locations.map((held) => (
          <TableRow key={held.id}>
            <TableCell>{held.name ?? held.external_id}</TableCell>
            <TableCell>
              {changes ? (
                <IdSelect
                  value={held.location_id}
                  options={options}
                  none={t("section.nothing")}
                  onChange={(locationId) =>
                    map.mutate({
                      storeId: store.id,
                      storeLocationId: held.id,
                      data: { location_id: locationId },
                    })
                  }
                />
              ) : (
                sellsFrom(held)
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
