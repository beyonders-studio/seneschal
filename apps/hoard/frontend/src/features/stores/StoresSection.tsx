import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  JobState,
  type StoreLocationRead,
  type StoreRead,
  StoreState,
} from "@/api/generated/hoard.schemas";
import { useListSettingsLocations } from "@/api/generated/settings/settings";
import {
  getListStoresQueryKey,
  useListStores,
  useMapStoreLocation,
  useRefreshStore,
  useUnsyncStore,
} from "@/api/generated/stores/stores";
import { JobProgress } from "@/components/JobProgress";
import { toast } from "@/components/Toaster";
import { IdSelect } from "@/features/items/IdSelect";
import { useChangesSettings } from "@/features/settings/NamedList";
import { useLocationName } from "@/features/settings/names";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { PushLog } from "@/features/stores/PushLog";
import { DifferingFields, StoreFieldsSettings } from "@/features/stores/StoreFields";
import { SyncWizard } from "@/features/stores/SyncWizard";
import { useStoreName, useStoreReason } from "@/features/stores/words";
import { errorMessage } from "@/lib/errors";
import { jobFinished, useJob } from "@/lib/jobs";
import { Alert, AlertDescription } from "@/ui/alert";
import { Badge } from "@/ui/badge";
import { Button } from "@/ui/button";
import { ConfirmDialog } from "@/ui/confirm-dialog";
import { RelativeTime } from "@/ui/relative-time";
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
  const [showingFields, setShowingFields] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [removing, setRemoving] = useState(false);
  const refresh = useRefreshStore({
    mutation: {
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: getListStoresQueryKey() });
        toast.success(t("section.refreshed"));
      },
      onError: (error) => toast.error(errorMessage(error, t, "section.refreshFailed")),
    },
  });
  const unsync = useUnsyncStore({
    mutation: {
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: getListStoresQueryKey() });
        setRemoving(false);
        toast.success(t("sync.removed"));
      },
      onError: (error) => {
        setRemoving(false);
        toast.error(errorMessage(error, t, "sync.removeFailed"));
      },
    },
  });
  const synced = store.state === StoreState.synced;
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
            <Badge variant={store.connected && synced ? "secondary" : "outline"}>
              {store.connected ? t(`section.states.${store.state}`) : t("section.notConnected")}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground">
            {[t(`channels.${store.channel}`), store.shop_domain].filter(Boolean).join(" · ")}
          </p>
          {synced && store.imported_at && (
            <p className="text-sm text-muted-foreground">
              {t("section.syncedWhen")} <RelativeTime date={store.imported_at} />
            </p>
          )}
          {synced && <p className="text-sm text-muted-foreground">{counts.join(" · ")}</p>}
        </div>
        {changes && store.connected && (
          <div className="flex flex-wrap gap-2">
            {store.state === StoreState.pending && (
              <Button onClick={() => setSyncing(true)}>
                {t("sync.open", { channel: t(`channels.${store.channel}`) })}
              </Button>
            )}
            {synced && (
              <>
                <Button
                  variant="outline"
                  pending={refresh.isPending}
                  onClick={() => refresh.mutate({ storeId: store.id })}
                >
                  <RefreshCw aria-hidden />
                  {t("section.refresh")}
                </Button>
                <Button variant="outline" onClick={() => setRemoving(true)}>
                  {t("sync.remove")}
                </Button>
              </>
            )}
          </div>
        )}
      </div>
      {store.last_check_error && (
        <Alert variant="destructive">
          <AlertDescription>{reason(store.last_check_error)}</AlertDescription>
        </Alert>
      )}
      {!synced && store.sync_job_id !== null && (
        <SyncProgress store={store} jobId={store.sync_job_id} />
      )}
      {synced && (
        <>
          <StoreLocations store={store} changes={changes} />
          <DifferingFields store={store} changes={changes} />
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" onClick={() => setShowingFields(!showingFields)}>
              {showingFields ? t("section.hideFields") : t("section.showFields")}
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setShowingLog(!showingLog)}>
              {showingLog ? t("section.hideLog") : t("section.showLog")}
            </Button>
          </div>
          {showingFields && <StoreFieldsSettings store={store} changes={changes} />}
          {showingLog && <PushLog storeId={store.id} />}
        </>
      )}
      <SyncWizard store={store} open={syncing} onOpenChange={setSyncing} />
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={t("sync.removeTitle", { store: name(store) })}
        description={t("sync.removeBody")}
        confirmLabel={t("sync.remove")}
        destructive
        isLoading={unsync.isPending}
        onConfirm={(event) => {
          event.preventDefault();
          unsync.mutate({ storeId: store.id });
        }}
      />
    </article>
  );
}

/** How the store's sync is going, or why it failed; once it finishes, the
 *  store is read again for where it now stands. */
function SyncProgress({ store, jobId }: { store: StoreRead; jobId: number }) {
  const queryClient = useQueryClient();
  const { data: job } = useJob(jobId);
  const finished = jobFinished(job);
  useEffect(() => {
    if (finished && store.state === StoreState.syncing) {
      void queryClient.invalidateQueries({ queryKey: getListStoresQueryKey() });
    }
  }, [finished, store.state, queryClient]);
  if (!job || job.state === JobState.done) return null;
  return <JobProgress job={job} />;
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
