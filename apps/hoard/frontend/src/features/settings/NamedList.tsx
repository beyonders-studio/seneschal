import { type QueryKey, useMutation, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { type FormEvent, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { toast } from "@/components/Toaster";
import { errorMessage } from "@/lib/errors";
import { useIsAdmin } from "@/lib/permissions";
import { useSession } from "@/session";
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
import { Table, TableBody, TableCell, TableRow } from "@/ui/table";

interface Named {
  id: number;
  name: string | null;
}

/** Whether the reader may change Hoard's settings: an admin, while Hoard may change. */
export function useChangesSettings(): boolean {
  const admin = useIsAdmin();
  const { read_only } = useSession();
  return admin && !read_only;
}

/**
 * A list of named settings, such as units, that an admin adds to, renames
 * and removes; everyone else reads it. Its words are `settings.<words>.*` in
 * the catalog namespace: `add`, `empty` and `name`.
 */
export function NamedList<T extends Named>({
  words,
  items,
  queryKey,
  nameOf = (item) => item.name ?? "",
  add,
  rename,
  remove,
  removable = () => true,
  detail,
}: {
  words: string;
  items: T[] | undefined;
  /** The list's query, which every change refreshes. */
  queryKey: QueryKey;
  nameOf?: (item: T) => string;
  add: (name: string) => Promise<unknown>;
  rename: (item: T, name: string) => Promise<unknown>;
  remove: (item: T) => Promise<unknown>;
  removable?: (item: T) => boolean;
  /** What else a row shows of its item, beside its name. */
  detail?: (item: T) => ReactNode;
}) {
  const { t } = useTranslation("catalog");
  const changes = useChangesSettings();
  const queryClient = useQueryClient();
  const [naming, setNaming] = useState<T | "new" | null>(null);
  const [removing, setRemoving] = useState<T | null>(null);
  const change = useMutation({
    mutationFn: (work: () => Promise<unknown>) => work(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
  });

  const removeChosen = () => {
    if (!removing) return;
    change.mutate(() => remove(removing), {
      onError: (error) => toast.error(errorMessage(error, t, "settings.removeFailed")),
    });
  };

  return (
    <div className="flex flex-col items-start gap-2">
      {items?.length === 0 && (
        <p className="text-sm text-muted-foreground">{t(`settings.${words}.empty`)}</p>
      )}
      {!!items?.length && (
        <Table>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.id}>
                <TableCell>{nameOf(item)}</TableCell>
                {detail && <TableCell className="text-right">{detail(item)}</TableCell>}
                <TableCell className="w-12">
                  <RowActionsMenu subject={nameOf(item)}>
                    {changes && (
                      <DropdownMenuItem onSelect={() => setNaming(item)}>
                        {t("settings.rename")}
                      </DropdownMenuItem>
                    )}
                    {changes && removable(item) && (
                      <DropdownMenuItem
                        className="text-destructive"
                        onSelect={() => setRemoving(item)}
                      >
                        {t("hoard:common.delete")}
                      </DropdownMenuItem>
                    )}
                  </RowActionsMenu>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {changes && (
        <Button variant="outline" size="sm" onClick={() => setNaming("new")}>
          <Plus aria-hidden />
          {t(`settings.${words}.add`)}
        </Button>
      )}
      <Dialog open={naming !== null} onOpenChange={(open) => !open && setNaming(null)}>
        <DialogContent>
          {naming !== null && (
            <NameForm
              title={naming === "new" ? t(`settings.${words}.add`) : t("settings.rename")}
              label={t(`settings.${words}.name`)}
              initial={naming === "new" ? "" : nameOf(naming)}
              pending={change.isPending}
              save={(name, done) =>
                change.mutate(() => (naming === "new" ? add(name) : rename(naming, name)), {
                  onSuccess: () => setNaming(null),
                  onError: (error) => done(errorMessage(error, t, "settings.saveFailed")),
                })
              }
            />
          )}
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => !open && setRemoving(null)}
        title={t("settings.removeTitle", { name: removing ? nameOf(removing) : "" })}
        confirmLabel={t("hoard:common.delete")}
        destructive
        onConfirm={removeChosen}
      />
    </div>
  );
}

/** The one field a named setting has, in its dialog. */
export function NameForm({
  title,
  label,
  initial,
  pending,
  save,
}: {
  title: string;
  label: string;
  initial: string;
  pending: boolean;
  /** Saves `name`; `done` takes why it could not be. */
  save: (name: string, done: (error: string) => void) => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(initial);
  const [error, setError] = useState<string>();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save(name.trim(), setError);
  };
  return (
    <form onSubmit={submit} className="contents">
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
      </DialogHeader>
      <DialogBody>
        <Field label={label} error={error}>
          <Input value={name} onChange={(event) => setName(event.target.value)} required />
        </Field>
      </DialogBody>
      <DialogFooter
        submitLabel={t("common.save")}
        pending={pending}
        pendingLabel={t("common.saving")}
        disabled={!name.trim()}
      />
    </form>
  );
}
