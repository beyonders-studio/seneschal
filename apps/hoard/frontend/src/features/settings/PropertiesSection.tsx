import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import { type FormEvent, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  type HoardPropertyOption,
  type HoardPropertyRead,
  RecordKind,
} from "@/api/generated/hoard.schemas";
import {
  deleteProperty,
  getListPropertiesQueryKey,
  updateProperty,
  useListProperties,
} from "@/api/generated/properties/properties";
import { toast } from "@/components/Toaster";
import { NameForm, useChangesSettings } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { errorMessage } from "@/lib/errors";
import { useIsAdmin } from "@/lib/permissions";
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
import { Input } from "@/ui/input";
import { normalizeOptionList } from "@/ui/properties/propertyHelpers";
import { iconForPropertyType } from "@/ui/properties/propertyTypeIcons";
import { RowActionsMenu } from "@/ui/row-actions-menu";
import { Table, TableBody, TableCell, TableRow } from "@/ui/table";

/** The properties materials, products and lots have, each kind its own, which
 *  only an admin sees here. They are added on a record's page; here they are
 *  renamed, a select's options changed, or removed. */
export function PropertiesSection() {
  const { t } = useTranslation("fields");
  if (!useIsAdmin()) return null;
  return (
    <SettingsSection id="properties" title={t("properties")}>
      <p className="text-sm text-muted-foreground">{t("settings.help")}</p>
      {Object.values(RecordKind).map((kind) => (
        <KindProperties key={kind} kind={kind} />
      ))}
    </SettingsSection>
  );
}

function KindProperties({ kind }: { kind: RecordKind }) {
  const { t } = useTranslation(["fields", "properties"]);
  const changes = useChangesSettings();
  const queryClient = useQueryClient();
  const properties = useListProperties({ kind }).data;
  const [renaming, setRenaming] = useState<HoardPropertyRead | null>(null);
  const [choosing, setChoosing] = useState<HoardPropertyRead | null>(null);
  const [removing, setRemoving] = useState<HoardPropertyRead | null>(null);
  const change = useMutation({
    mutationFn: (work: () => Promise<unknown>) => work(),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: getListPropertiesQueryKey({ kind }) }),
  });

  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-sm font-medium">{t(`settings.kinds.${kind}`)}</h3>
      {properties?.length === 0 && (
        <p className="text-sm text-muted-foreground">{t("settings.empty")}</p>
      )}
      {!!properties?.length && (
        <Table>
          <TableBody>
            {properties.map((property) => {
              const Icon = iconForPropertyType(property.type);
              return (
                <TableRow key={property.id}>
                  <TableCell>{property.name}</TableCell>
                  <TableCell className="text-muted-foreground">
                    <span className="inline-flex items-center gap-1.5">
                      <Icon className="size-3.5" aria-hidden />
                      {t(`properties:types.${property.type}`)}
                    </span>
                  </TableCell>
                  <TableCell className="w-12">
                    <RowActionsMenu subject={property.name}>
                      {changes && (
                        <DropdownMenuItem onSelect={() => setRenaming(property)}>
                          {t("settings.rename")}
                        </DropdownMenuItem>
                      )}
                      {changes && property.options && (
                        <DropdownMenuItem onSelect={() => setChoosing(property)}>
                          {t("settings.options")}
                        </DropdownMenuItem>
                      )}
                      {changes && (
                        <DropdownMenuItem
                          className="text-destructive"
                          onSelect={() => setRemoving(property)}
                        >
                          {t("hoard:common.delete")}
                        </DropdownMenuItem>
                      )}
                    </RowActionsMenu>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
      <Dialog open={renaming !== null} onOpenChange={(open) => !open && setRenaming(null)}>
        <DialogContent>
          {renaming && (
            <NameForm
              title={t("settings.rename")}
              label={t("hoard:common.name")}
              initial={renaming.name}
              pending={change.isPending}
              save={(name, done) =>
                change.mutate(() => updateProperty(renaming.id, { name }), {
                  onSuccess: () => setRenaming(null),
                  onError: (error) => done(errorMessage(error, t, "saveFailed")),
                })
              }
            />
          )}
        </DialogContent>
      </Dialog>
      <Dialog open={choosing !== null} onOpenChange={(open) => !open && setChoosing(null)}>
        <DialogContent>
          {choosing && (
            <OptionsForm
              property={choosing}
              pending={change.isPending}
              save={(options, done) =>
                change.mutate(() => updateProperty(choosing.id, { options }), {
                  onSuccess: () => setChoosing(null),
                  onError: (error) => done(errorMessage(error, t, "saveFailed")),
                })
              }
            />
          )}
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={t("settings.removeTitle", { name: removing?.name ?? "" })}
        description={t("settings.removeBody")}
        confirmLabel={t("hoard:common.delete")}
        destructive
        onConfirm={() =>
          removing &&
          change.mutate(() => deleteProperty(removing.id), {
            onError: (error) => toast.error(errorMessage(error, t, "settings.removeFailed")),
          })
        }
      />
    </div>
  );
}

/** A select's options, each labelled, in its dialog. An option added takes
 *  its value from its label; one kept keeps its value, which records hold. */
function OptionsForm({
  property,
  pending,
  save,
}: {
  property: HoardPropertyRead;
  pending: boolean;
  /** Saves `options`; `done` takes why they could not be. */
  save: (options: HoardPropertyOption[], done: (error: string) => void) => void;
}) {
  const { t } = useTranslation(["fields", "properties", "hoard"]);
  const [options, setOptions] = useState<HoardPropertyOption[]>(property.options ?? []);
  const [error, setError] = useState<string>();
  const kept = normalizeOptionList(options);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save(kept, setError);
  };
  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{t("settings.optionsTitle", { name: property.name })}</DialogTitle>
      </DialogHeader>
      <DialogBody className="flex flex-col items-start gap-2">
        {options.map((option, index) => (
          // Keyed by place: a key from the label being typed would remount
          // the input on each keystroke.
          // biome-ignore lint/suspicious/noArrayIndexKey: rows change only by adding and removing
          <div key={index} className="flex w-full items-center gap-2">
            <Input
              aria-label={t("properties:manager.optionLabelPlaceholder")}
              placeholder={t("properties:manager.optionLabelPlaceholder")}
              value={option.label}
              onChange={(event) =>
                setOptions(
                  options.map((each, at) =>
                    at === index ? { ...each, label: event.target.value } : each
                  )
                )
              }
            />
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={t("properties:manager.removeOption")}
              onClick={() => setOptions(options.filter((_, at) => at !== index))}
            >
              <X aria-hidden />
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => setOptions([...options, { value: "", label: "" }])}
        >
          <Plus aria-hidden />
          {t("properties:manager.addOption")}
        </Button>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
      </DialogBody>
      <DialogFooter
        submitLabel={t("hoard:common.save")}
        pending={pending}
        pendingLabel={t("hoard:common.saving")}
        disabled={kept.length === 0}
      />
    </form>
  );
}
