import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  Allergen,
  FieldTarget,
  type HoardPropertyRead,
  type JobRead,
  RecordKind,
  type StoreFieldChoiceWrite,
  type StoreFieldRead,
  type StoreFieldTargetRead,
  type StoreRead,
} from "@/api/generated/hoard.schemas";
import { useListProperties } from "@/api/generated/properties/properties";
import {
  getListStoreFieldDifferencesQueryKey,
  getListStoreFieldsQueryKey,
  useListStoreFieldDifferences,
  useListStoreFields,
  useMapStoreField,
  useSendStoreFields,
  useUnmapStoreField,
} from "@/api/generated/stores/stores";
import { JobProgress } from "@/components/JobProgress";
import { toast } from "@/components/Toaster";
import { errorMessage } from "@/lib/errors";
import { jobFinished, useJob } from "@/lib/jobs";
import { Button } from "@/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/ui/select";
import { StatusMessage } from "@/ui/status-message";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** What a store's field fills, as chosen: a derived field or a property (a
 *  new one for a null id), with an allergens field's values paired; null to
 *  leave it out. `unpaired` are the values nothing paired to begin with,
 *  which are offered to pair by hand. */
export type FieldChoice = {
  target: FieldTarget;
  property_id: number | null;
  pairs: Record<string, Allergen | null>;
  unpaired: string[];
} | null;

/** Each field's choice, by `fieldKey`. */
export type FieldChoices = Record<string, FieldChoice>;

export const fieldKey = (field: { owner: string; handle: string }) =>
  `${field.owner}:${field.handle}`;

/** A choice from what a field fills or is suggested to fill. */
function choiceOf(held: StoreFieldTargetRead | null): FieldChoice {
  if (!held) return null;
  return {
    target: held.target,
    property_id: held.property_id,
    pairs: { ...held.pairs },
    unpaired: Object.entries(held.pairs)
      .filter(([, allergen]) => allergen === null)
      .map(([value]) => value),
  };
}

/** Each field's choice to begin with: Hoard's suggestion. */
export function suggestedChoices(found: StoreFieldRead[]): FieldChoices {
  return Object.fromEntries(found.map((field) => [fieldKey(field), choiceOf(field.suggested)]));
}

/** The fields a sync is sent with: each one chosen, the rest left out. */
export function fieldsBody(choices: FieldChoices): StoreFieldChoiceWrite[] {
  return Object.entries(choices).flatMap(([key, choice]) => {
    if (!choice) return [];
    const at = key.indexOf(":");
    return [
      {
        owner: key.slice(0, at) as StoreFieldChoiceWrite["owner"],
        handle: key.slice(at + 1),
        target: choice.target,
        property_id: choice.property_id,
        pairs: choice.target === FieldTarget.allergens ? choice.pairs : {},
      },
    ];
  });
}

/** The value a field's choice is selected by. */
const OUT = "out";
const NEW = "new";
const selected = (choice: FieldChoice) =>
  !choice
    ? OUT
    : choice.target !== FieldTarget.property
      ? choice.target
      : choice.property_id === null
        ? NEW
        : `property:${choice.property_id}`;
function chosen(value: string, before: FieldChoice, field: StoreFieldRead): FieldChoice {
  if (value === OUT) return null;
  const pairs = before?.pairs ??
    choiceOf(field.mapped ?? field.suggested)?.pairs ?? {
      ...Object.fromEntries(field.entries.map((entry) => [entry.value, null])),
    };
  const target = value === FieldTarget.allergens || value === FieldTarget.ingredients;
  return {
    target: target ? (value as FieldTarget) : FieldTarget.property,
    property_id: value.startsWith("property:") ? Number(value.slice("property:".length)) : null,
    pairs,
    unpaired:
      before?.unpaired ??
      Object.entries(pairs)
        .filter(([, allergen]) => allergen === null)
        .map(([held]) => held),
  };
}

/** One field: its name, type and how many hold it, beside what it fills,
 *  with an allergens field's unpaired values to pair. */
function FieldRow({
  field,
  choice,
  properties,
  disabled,
  onChange,
}: {
  field: StoreFieldRead;
  choice: FieldChoice;
  properties: HoardPropertyRead[];
  disabled?: boolean;
  onChange: (choice: FieldChoice) => void;
}) {
  const { t } = useTranslation(["stores", "declarations"]);
  const fitting = properties.filter((property) => property.type === field.property_type);
  const names = Object.fromEntries(field.entries.map((entry) => [entry.value, entry.name]));
  return (
    <TableRow>
      <TableCell>
        <div className="flex flex-col">
          <span>{field.name}</span>
          <span className="text-xs text-muted-foreground">
            {[
              field.handle,
              t(`fields.owners.${field.owner}`),
              field.type,
              t("fields.count", { count: field.count }),
            ].join(" · ")}
          </span>
        </div>
      </TableCell>
      <TableCell>
        <div className="flex flex-col gap-2">
          <Select
            value={selected(choice)}
            disabled={disabled}
            onValueChange={(value) => onChange(chosen(value, choice, field))}
          >
            <SelectTrigger aria-label={field.name}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {field.allergens && (
                <SelectItem value={FieldTarget.allergens}>{t("fields.allergens")}</SelectItem>
              )}
              {field.ingredients && (
                <SelectItem value={FieldTarget.ingredients}>{t("fields.ingredients")}</SelectItem>
              )}
              {fitting.map((property) => (
                <SelectItem key={property.id} value={`property:${property.id}`}>
                  {t("fields.property", { name: property.name })}
                </SelectItem>
              ))}
              {field.property_type && (
                <SelectItem value={NEW}>
                  {t("fields.newProperty", {
                    name: field.name,
                    type: t(`fields.types.${field.property_type}`),
                  })}
                </SelectItem>
              )}
              <SelectItem value={OUT}>{t("fields.out")}</SelectItem>
            </SelectContent>
          </Select>
          {choice?.target === FieldTarget.allergens && choice.unpaired.length > 0 && (
            <div className="flex flex-col gap-1">
              <span className="text-xs text-muted-foreground">{t("fields.pairHint")}</span>
              {choice.unpaired.map((value) => (
                <Select
                  key={value}
                  value={choice.pairs[value] ?? OUT}
                  disabled={disabled}
                  onValueChange={(allergen) =>
                    onChange({
                      ...choice,
                      pairs: {
                        ...choice.pairs,
                        [value]: allergen === OUT ? null : (allergen as Allergen),
                      },
                    })
                  }
                >
                  <SelectTrigger aria-label={t("fields.pair", { value: names[value] ?? value })}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.values(Allergen).map((allergen) => (
                      <SelectItem key={allergen} value={allergen}>
                        {t(`declarations:allergens.${allergen}`)}
                      </SelectItem>
                    ))}
                    <SelectItem value={OUT}>{t("fields.notAllergen")}</SelectItem>
                  </SelectContent>
                </Select>
              ))}
            </div>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

/** The store's fields, beside what each fills. */
function FieldTable({
  found,
  choices,
  properties,
  disabled,
  onChange,
}: {
  found: StoreFieldRead[];
  choices: FieldChoices;
  properties: HoardPropertyRead[];
  disabled?: boolean;
  onChange: (field: StoreFieldRead, choice: FieldChoice) => void;
}) {
  const { t } = useTranslation("stores");
  if (found.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("fields.none")}</p>;
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{t("fields.storeField")}</TableHead>
          <TableHead>{t("fields.fills")}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {found.map((field) => (
          <FieldRow
            key={fieldKey(field)}
            field={field}
            choice={choices[fieldKey(field)] ?? null}
            properties={properties}
            disabled={disabled}
            onChange={(choice) => onChange(field, choice)}
          />
        ))}
      </TableBody>
    </Table>
  );
}

/** The store's fields and its product properties, read; or why they couldn't be. */
function useFieldsRead(store: StoreRead) {
  const found = useListStoreFields(store.id);
  const properties = useListProperties({ kind: RecordKind.product });
  return { found, properties };
}

/** The sync wizard's step: each of the store's fields beside what it fills,
 *  each starting as Hoard suggests. */
export function FieldsStep({
  store,
  choices,
  onChange,
}: {
  store: StoreRead;
  choices: FieldChoices | null;
  onChange: (choices: FieldChoices) => void;
}) {
  const { t } = useTranslation("stores");
  const { found, properties } = useFieldsRead(store);
  useEffect(() => {
    if (choices === null && found.data) onChange(suggestedChoices(found.data));
  }, [choices, found.data, onChange]);
  if (found.isError) {
    return (
      <StatusMessage
        title={errorMessage(found.error, t, "fields.loadFailed")}
        onRetry={() => void found.refetch()}
      />
    );
  }
  if (!found.data || !properties.data || choices === null) {
    return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm">{t("fields.explain")}</p>
      <FieldTable
        found={found.data}
        choices={choices}
        properties={properties.data}
        onChange={(field, choice) => onChange({ ...choices, [fieldKey(field)]: choice })}
      />
    </div>
  );
}

/** Settings › Stores › Fields: a synced store's fields beside what each
 *  fills, each change saved at once, and a newly mapped field read in. */
export function StoreFieldsSettings({ store, changes }: { store: StoreRead; changes: boolean }) {
  const { t } = useTranslation("stores");
  const queryClient = useQueryClient();
  const { found, properties } = useFieldsRead(store);
  const [reading, setReading] = useState<number | null>(null);
  const refreshed = async () => {
    await queryClient.invalidateQueries({ queryKey: getListStoreFieldsQueryKey(store.id) });
    toast.success(t("fields.saved"));
  };
  const map = useMapStoreField({
    mutation: {
      onSuccess: async (job: JobRead) => {
        setReading(job.id);
        await refreshed();
      },
      onError: (error) => toast.error(errorMessage(error, t, "fields.saveFailed")),
    },
  });
  const unmap = useUnmapStoreField({
    mutation: {
      onSuccess: refreshed,
      onError: (error) => toast.error(errorMessage(error, t, "fields.saveFailed")),
    },
  });
  if (found.isError) {
    return (
      <StatusMessage
        title={errorMessage(found.error, t, "fields.loadFailed")}
        onRetry={() => void found.refetch()}
      />
    );
  }
  if (!found.data || !properties.data) {
    return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;
  }
  const choices = Object.fromEntries(
    found.data.map((field) => [fieldKey(field), choiceOf(field.mapped)])
  );
  return (
    <div className="flex flex-col gap-3">
      <FieldTable
        found={found.data}
        choices={choices}
        properties={properties.data}
        disabled={!changes || map.isPending || unmap.isPending}
        onChange={(field, choice) => {
          if (choice) {
            const [data] = fieldsBody({ [fieldKey(field)]: choice });
            map.mutate({ storeId: store.id, data });
          } else if (field.mapping_id !== null) {
            unmap.mutate({ storeId: store.id, mappingId: field.mapping_id });
          }
        }}
      />
      {reading !== null && <FieldJob jobId={reading} store={store} />}
    </div>
  );
}

/** How a fields job is going; once done, the differing values are read again. */
function FieldJob({ jobId, store }: { jobId: number; store: StoreRead }) {
  const queryClient = useQueryClient();
  const { data: job } = useJob(jobId);
  const finished = jobFinished(job);
  useEffect(() => {
    if (finished) {
      void queryClient.invalidateQueries({
        queryKey: getListStoreFieldDifferencesQueryKey(store.id),
      });
    }
  }, [finished, store.id, queryClient]);
  return job ? <JobProgress job={job} /> : null;
}

/** The products whose allergens or ingredient list differ between Hoard and
 *  the store, with **Send Hoard's values** for an admin; nothing is sent
 *  until it is clicked. */
export function DifferingFields({ store, changes }: { store: StoreRead; changes: boolean }) {
  const { t } = useTranslation("stores");
  const differing = useListStoreFieldDifferences(store.id);
  const [sending, setSending] = useState<number | null>(null);
  const send = useSendStoreFields({
    mutation: {
      onSuccess: (job: JobRead) => setSending(job.id),
      onError: (error) => toast.error(errorMessage(error, t, "fields.sendFailed")),
    },
  });
  const rows = differing.data ?? [];
  if (rows.length === 0 && sending === null) return null;
  const channel = t(`channels.${store.channel}`);
  const listed = (values: string[]) => values.join(", ") || t("fields.nothing");
  return (
    <section aria-label={t("fields.differing")} className="flex flex-col gap-2">
      <h4 className="text-sm font-medium">{t("fields.differing")}</h4>
      <p className="text-sm text-muted-foreground">{t("fields.differingHint", { channel })}</p>
      {rows.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("fields.product")}</TableHead>
              <TableHead>{t("fields.storeField")}</TableHead>
              <TableHead>{t("fields.inHoard")}</TableHead>
              <TableHead>{t("fields.inStore", { channel })}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <TableRow key={`${row.mapping_id}:${row.owner_id}`}>
                <TableCell>{row.product_name ?? row.owner_id}</TableCell>
                <TableCell>{row.field}</TableCell>
                <TableCell>{listed(row.hoard)}</TableCell>
                <TableCell>{listed(row.store)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {changes && rows.length > 0 && (
        <div>
          <Button
            variant="outline"
            pending={send.isPending}
            onClick={() => send.mutate({ storeId: store.id })}
          >
            {t("fields.send", { channel })}
          </Button>
        </div>
      )}
      {sending !== null && <FieldJob jobId={sending} store={store} />}
    </section>
  );
}
