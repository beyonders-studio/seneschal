import { useMutation } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowLeft, Pencil, Trash2 } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";

import { toast } from "@/components/Toaster";
import { errorMessage } from "@/lib/errors";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { ConfirmDialog } from "@/ui/confirm-dialog";

/**
 * The top of an item's page: the way back to its list, its name and marks,
 * and, while Hoard may change, editing and deleting it. Deleting asks first.
 */
export function ItemHeader({
  back,
  title,
  marks,
  onEdit,
  remove,
  onRemoved,
}: {
  back: { to: "/stock/materials" | "/stock/products"; label: string };
  title: string;
  marks?: ReactNode;
  onEdit: () => void;
  remove: () => Promise<unknown>;
  onRemoved: () => void;
}) {
  const { t } = useTranslation("catalog");
  const { read_only } = useSession();
  const [removing, setRemoving] = useState(false);
  const removal = useMutation({
    mutationFn: remove,
    onSuccess: onRemoved,
    onError: (error) => toast.error(errorMessage(error, t, "items.removeFailed")),
  });
  return (
    <div className="flex flex-col gap-2">
      <Link
        to={back.to}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:underline"
      >
        <ArrowLeft aria-hidden className="size-4" />
        {back.label}
      </Link>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
          {marks}
        </div>
        {!read_only && (
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onEdit}>
              <Pencil aria-hidden />
              {t("hoard:common.edit")}
            </Button>
            <Button variant="outline" size="sm" onClick={() => setRemoving(true)}>
              <Trash2 aria-hidden />
              {t("hoard:common.delete")}
            </Button>
          </div>
        )}
      </div>
      <ConfirmDialog
        open={removing}
        onOpenChange={setRemoving}
        title={t("items.removeTitle", { name: title })}
        description={t("items.removeBody")}
        confirmLabel={t("hoard:common.delete")}
        destructive
        isLoading={removal.isPending}
        onConfirm={() => removal.mutate()}
      />
    </div>
  );
}

/** What an item is, as labelled facts; one left blank is left out. */
export function ItemFacts({ facts }: { facts: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-pair gap-x-6 gap-y-3">
      {facts
        .filter(([, value]) => Boolean(value))
        .map(([label, value]) => (
          <div key={label} className="flex flex-col gap-0.5">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="text-sm">{value}</dd>
          </div>
        ))}
    </dl>
  );
}
