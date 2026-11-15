import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  SettingsLocationRead,
  StoreLocationFound,
  StoreRead,
  StoreSyncWrite,
} from "@/api/generated/hoard.schemas";
import { useListSettingsLocations } from "@/api/generated/settings/settings";
import {
  getListStoresQueryKey,
  useReadStoreLocations,
  useSyncStore,
} from "@/api/generated/stores/stores";
import { toast } from "@/components/Toaster";
import { useLocationName } from "@/features/settings/names";
import { errorMessage } from "@/lib/errors";
import { DialogBody, DialogFooter } from "@/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";
import { WizardDialog } from "@/ui/wizard-dialog";

/** Where a store location's stock goes: a Hoard location by its id, a new
 *  location named as the store names it, or nowhere. */
export type LocationChoice = { location: number } | "new" | "nothing";

/** What the wizard's steps have chosen, sent with the sync once it starts.
 *  Each store location's choice, by the store's id for it, once its step has
 *  read them. */
export interface SyncDraft {
  locations: Record<string, LocationChoice> | null;
}

/** The sync a draft asks for. */
export function syncBody(draft: SyncDraft): StoreSyncWrite {
  return {
    locations: Object.entries(draft.locations ?? {}).flatMap(([external_id, choice]) =>
      choice === "nothing"
        ? []
        : [
            choice === "new"
              ? { external_id, new: true }
              : { external_id, location_id: choice.location },
          ]
    ),
  };
}

/** Each store location's choice to begin with: the store's first location
 *  into Hoard's main location, and every other into a new one of its name. */
export function defaultChoices(
  found: StoreLocationFound[],
  locations: SettingsLocationRead[]
): Record<string, LocationChoice> {
  const main = locations.find((location) => location.main);
  return Object.fromEntries(
    found.map((location, index) => [
      location.external_id,
      index === 0 && main ? { location: main.id } : "new",
    ])
  );
}

/** One step of the wizard: the line under its title, what it shows, and
 *  whether it is ready to move on from. */
interface Step {
  id: string;
  description: string;
  body: ReactNode;
  ready: boolean;
}

/**
 * Syncing a pending store, for an admin: what syncing does, where each store
 * location's stock goes, then what happens afterwards, with **Sync now**.
 * Each step is an entry of `steps`, between the first and the last, reading
 * and setting the one `draft` the sync is sent from (`syncBody`).
 */
export function SyncWizard({
  store,
  open,
  onOpenChange,
}: {
  store: StoreRead;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("stores");
  const queryClient = useQueryClient();
  const channel = t(`channels.${store.channel}`);
  const [at, setAt] = useState(0);
  const [draft, setDraft] = useState<SyncDraft>({ locations: null });
  const close = (next: boolean) => {
    if (!next) {
      setAt(0);
      setDraft({ locations: null });
    }
    onOpenChange(next);
  };
  const sync = useSyncStore({
    mutation: {
      onSuccess: async () => {
        await queryClient.invalidateQueries({ queryKey: getListStoresQueryKey() });
        toast.success(t("sync.started"));
        close(false);
      },
      onError: (error) => toast.error(errorMessage(error, t, "sync.startFailed")),
    },
  });

  const steps: Step[] = [
    {
      id: "about",
      description: t("sync.about.description"),
      body: <Words keys={["sync.about.reads", "sync.about.replaces", "sync.about.sends"]} />,
      ready: true,
    },
    {
      id: "locations",
      description: t("sync.locations.description"),
      body: (
        <LocationsStep
          store={store}
          choices={draft.locations}
          onChange={(locations) => setDraft((now) => ({ ...now, locations }))}
        />
      ),
      ready: draft.locations !== null,
    },
    {
      id: "after",
      description: t("sync.after.description"),
      body: <Words keys={["sync.after.keeps", "sync.after.remove"]} />,
      ready: true,
    },
  ];
  const step = steps[at];
  const last = at === steps.length - 1;

  return (
    <WizardDialog
      open={open}
      onOpenChange={close}
      title={t("sync.title", { channel })}
      description={step.description}
      progress={{ current: at + 1, total: steps.length }}
      onBack={at > 0 ? () => setAt(at - 1) : undefined}
      backLabel={t("hoard:common.back")}
      backDisabled={sync.isPending}
    >
      <DialogBody>{step.body}</DialogBody>
      <DialogFooter
        submitLabel={last ? t("sync.now") : t("sync.next")}
        cancel={t("hoard:common.cancel")}
        pending={sync.isPending}
        disabled={!step.ready}
        onSubmit={() =>
          last ? sync.mutate({ storeId: store.id, data: syncBody(draft) }) : setAt(at + 1)
        }
      />
    </WizardDialog>
  );
}

/** Paragraphs, each the words a key names. */
function Words({ keys }: { keys: string[] }) {
  const { t } = useTranslation("stores");
  return (
    <div className="flex flex-col gap-3 text-sm">
      {keys.map((key) => (
        <p key={key}>{t(key)}</p>
      ))}
    </div>
  );
}

/** The value a choice is selected by. */
const NEW = "new";
const NOTHING = "nothing";
const selected = (choice: LocationChoice) =>
  typeof choice === "object" ? String(choice.location) : choice;
const chosen = (value: string): LocationChoice =>
  value === NEW || value === NOTHING ? value : { location: Number(value) };

/** Each of the store's locations, read from the store now, beside where its
 *  stock goes. */
function LocationsStep({
  store,
  choices,
  onChange,
}: {
  store: StoreRead;
  choices: Record<string, LocationChoice> | null;
  onChange: (choices: Record<string, LocationChoice>) => void;
}) {
  const { t } = useTranslation("stores");
  const locationName = useLocationName();
  const found = useReadStoreLocations(store.id);
  const locations = useListSettingsLocations();
  // Chosen as the defaults once both are read; the step is ready from then.
  useEffect(() => {
    if (choices === null && found.data && locations.data) {
      onChange(defaultChoices(found.data, locations.data));
    }
  }, [choices, found.data, locations.data, onChange]);
  if (found.isError) {
    return (
      <StatusMessage
        title={errorMessage(found.error, t, "sync.locations.loadFailed")}
        onRetry={() => void found.refetch()}
      />
    );
  }
  if (!found.data || !locations.data || choices === null) {
    return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;
  }
  if (found.data.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("sync.locations.none")}</p>;
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("section.storeLocation")}</TableHead>
          <TableHead>{t("sync.locations.goesInto")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {found.data.map((location) => (
          <TableRow key={location.external_id}>
            <TableCell>{location.name || location.external_id}</TableCell>
            <TableCell>
              <Select
                value={selected(choices[location.external_id] ?? NOTHING)}
                onValueChange={(value) =>
                  onChange({ ...choices, [location.external_id]: chosen(value) })
                }
              >
                <SelectTrigger aria-label={location.name || location.external_id}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {locations.data.map((held) => (
                    <SelectItem key={held.id} value={String(held.id)}>
                      {locationName(held)}
                    </SelectItem>
                  ))}
                  <SelectItem value={NEW}>
                    {t("sync.locations.new", { name: location.name || location.external_id })}
                  </SelectItem>
                  <SelectItem value={NOTHING}>{t("section.nothing")}</SelectItem>
                </SelectContent>
              </Select>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
