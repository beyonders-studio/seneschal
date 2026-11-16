import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import type { SettingsPricePointRead } from "@/api/generated/hoard.schemas";
import {
  createSettingsPricePoint,
  deleteSettingsPricePoint,
  getListSettingsPricePointsQueryKey,
  updateSettingsPricePoint,
  useListSettingsPricePoints,
} from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { useShare } from "@/features/items/format";
import { useChangesSettings } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { errorMessage } from "@/lib/errors";
import { useMoney } from "@/lib/money";
import { useSeesCosts } from "@/lib/permissions";
import { Button } from "@/ui/button";
import { ConfirmDialog } from "@/ui/confirm-dialog";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/ui/dialog";
import { DropdownMenuItem } from "@/ui/dropdown-menu";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";
import { RowActionsMenu } from "@/ui/row-actions-menu";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** The numbers a price point holds, each with its words under
 *  `settings.pricePoints` in the catalog namespace. */
const WORDS = { markup: "markup", listing_fee: "listingFee", commission: "commission" } as const;
const NUMBERS = Object.keys(WORDS) as (keyof typeof WORDS)[];

/**
 * The price points the community sells at, such as Retail and Wholesale,
 * each with its markup on cost, its listing fee and its commission. An admin
 * adds, changes and removes them; they are costs, so shown only to a member
 * who sees costs.
 */
export function PricePointsSection() {
  const { t } = useTranslation("catalog");
  const seesCosts = useSeesCosts();
  const changes = useChangesSettings();
  const queryClient = useQueryClient();
  const money = useMoney();
  const share = useShare();
  const points = useListSettingsPricePoints({ query: { enabled: seesCosts } }).data;
  const [editing, setEditing] = useState<SettingsPricePointRead | "new" | null>(null);
  const [removing, setRemoving] = useState<SettingsPricePointRead | null>(null);
  const remove = useMutation({
    mutationFn: (point: SettingsPricePointRead) => deleteSettingsPricePoint(point.id),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: getListSettingsPricePointsQueryKey() }),
    onError: (error) => toast.error(errorMessage(error, t, "settings.removeFailed")),
  });
  if (!seesCosts || !points) return null;
  return (
    <SettingsSection id="price-points" title={t("settings.pricePoints.title")}>
      <p className="text-sm text-muted-foreground">{t("settings.pricePoints.description")}</p>
      {points.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("settings.pricePoints.empty")}</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("settings.pricePoints.name")}</TableHead>
              <TableHead className="text-right">{t("settings.pricePoints.markup")}</TableHead>
              <TableHead className="text-right">{t("settings.pricePoints.listingFee")}</TableHead>
              <TableHead className="text-right">{t("settings.pricePoints.commission")}</TableHead>
              {changes && <TableHead className="w-12" />}
            </TableRow>
          </TableHeader>
          <TableBody>
            {points.map((point) => (
              <TableRow key={point.id}>
                <TableCell>{point.name}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {point.markup && share(point.markup)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {point.listing_fee && money(point.listing_fee, { exact: true })}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {point.commission && share(point.commission)}
                </TableCell>
                {changes && (
                  <TableCell>
                    <RowActionsMenu subject={point.name}>
                      <DropdownMenuItem onSelect={() => setEditing(point)}>
                        {t("hoard:common.edit")}
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        className="text-destructive"
                        onSelect={() => setRemoving(point)}
                      >
                        {t("hoard:common.delete")}
                      </DropdownMenuItem>
                    </RowActionsMenu>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {changes && (
        <div>
          <Button variant="outline" size="sm" onClick={() => setEditing("new")}>
            <Plus aria-hidden />
            {t("settings.pricePoints.add")}
          </Button>
        </div>
      )}
      <Dialog open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {editing === "new" ? t("settings.pricePoints.add") : t("settings.pricePoints.edit")}
            </DialogTitle>
          </DialogHeader>
          {editing !== null && (
            <PricePointForm
              point={editing === "new" ? undefined : editing}
              onSaved={() => setEditing(null)}
            />
          )}
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={t("settings.removeTitle", { name: removing?.name ?? "" })}
        description={t("settings.pricePoints.removeBody")}
        confirmLabel={t("hoard:common.delete")}
        destructive
        onConfirm={() => removing && remove.mutate(removing)}
      />
    </SettingsSection>
  );
}

function PricePointForm({
  point,
  onSaved,
}: {
  point?: SettingsPricePointRead;
  onSaved: () => void;
}) {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const [name, setName] = useState(point?.name ?? "");
  const [numbers, setNumbers] = useState(() =>
    Object.fromEntries(NUMBERS.map((key) => [key, point?.[key] ?? ""]))
  );
  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: name.trim(),
        ...Object.fromEntries(NUMBERS.map((key) => [key, numbers[key].trim() || "0"])),
      };
      return point ? updateSettingsPricePoint(point.id, body) : createSettingsPricePoint(body);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: getListSettingsPricePointsQueryKey() });
      onSaved();
    },
    onError: (error) => toast.error(errorMessage(error, t, "settings.saveFailed")),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  return (
    <form onSubmit={submit} className="contents">
      <DialogBody className="space-y-4">
        <Field label={t("settings.pricePoints.name")}>
          <Input required value={name} onChange={(event) => setName(event.target.value)} />
        </Field>
        {NUMBERS.map((key) => (
          <Field
            key={key}
            label={t(`settings.pricePoints.${WORDS[key]}`)}
            hint={t(`settings.pricePoints.${WORDS[key]}Hint`)}
          >
            <Input
              inputMode="decimal"
              value={numbers[key]}
              onChange={(event) => setNumbers({ ...numbers, [key]: event.target.value })}
            />
          </Field>
        ))}
      </DialogBody>
      <DialogFooter>
        <Button type="submit" pending={save.isPending} disabled={!name.trim()}>
          {t("hoard:common.save")}
        </Button>
      </DialogFooter>
    </form>
  );
}
