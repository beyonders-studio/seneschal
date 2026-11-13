import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileText, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getListDocumentsQueryKey,
  linkDocument,
  unlinkDocument,
  useListDocuments,
} from "@/api/generated/documents/documents";
import { askFilesConsent } from "@/api/generated/files/files";
import type { ListDocumentsParams } from "@/api/generated/hoard.schemas";
import { toast } from "@/components/Toaster";
import { needsConsent, useInitiativeFiles } from "@/features/documents/files";
import { errorMessage } from "@/lib/errors";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { FileFieldInput } from "@/ui/properties/FileFieldInput";

/** What a document can be linked to. */
type Of = "lot" | "product" | "material" | "base";

const recordOf = (of: Of, id: number): ListDocumentsParams => {
  switch (of) {
    case "lot":
      return { lot_id: id };
    case "product":
      return { product_id: id };
    case "material":
      return { material_id: id };
    case "base":
      return { base_id: id };
  }
};

/**
 * The Initiative files linked to a lot, a product, a material or a base, in
 * the order they were linked, so several reports on one lot read together.
 * Each is named as the member viewing it may see it. While Hoard may change,
 * a member links another file they can open, or unlinks one; the file stays
 * in Initiative.
 */
export function DocumentsSection({ of, id }: { of: Of; id: number }) {
  const { t } = useTranslation("documents");
  const { read_only } = useSession();
  const record = recordOf(of, id);
  const { data: documents, error } = useListDocuments(record);
  const files = useInitiativeFiles();
  const queryClient = useQueryClient();
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: getListDocumentsQueryKey(record) });
  const link = useMutation({
    mutationFn: (fileId: number) => linkDocument({ file_id: fileId, ...record }),
    onSuccess: refresh,
    onError: (error) => toast.error(errorMessage(error, t, "section.linkFailed")),
  });
  const unlink = useMutation({
    mutationFn: (documentId: number) => unlinkDocument(documentId),
    onSuccess: refresh,
    onError: (error) => toast.error(errorMessage(error, t, "section.unlinkFailed")),
  });

  if (needsConsent(error)) return <ConsentNotice onGranted={refresh} />;
  if (error)
    return (
      <p className="text-sm text-muted-foreground">{errorMessage(error, t, "section.failed")}</p>
    );
  if (!documents) return null;
  return (
    <div className="flex flex-col gap-2">
      {documents.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("section.empty")}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {documents.map((linked) => {
            const name = linked.name ?? t("section.hidden");
            return (
              <li key={linked.id} className="flex min-w-0 items-center gap-2 text-sm">
                <FileText aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                <span
                  className={
                    linked.name ? "min-w-0 truncate" : "min-w-0 truncate text-muted-foreground"
                  }
                >
                  {name}
                </span>
                {!read_only && (
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="ml-auto"
                    aria-label={t("section.unlink", { name })}
                    disabled={unlink.isPending}
                    onClick={() => unlink.mutate(linked.id)}
                  >
                    <X aria-hidden />
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {!read_only && (
        <FileFieldInput
          value={null}
          shown={null}
          onChange={(fileId) => {
            if (fileId !== null) link.mutate(fileId);
          }}
          files={files}
          disabled={link.isPending}
        />
      )}
    </div>
  );
}

/** Asks the member to let Hoard read Initiative's files for them. They answer
 *  in Initiative, and the documents read again when they come back, or at
 *  once when they already have. */
function ConsentNotice({ onGranted }: { onGranted: () => void }) {
  const { t } = useTranslation("documents");
  const [asked, setAsked] = useState(false);
  const ask = useMutation({
    mutationFn: () => askFilesConsent(),
    onSuccess: ({ status }) => (status === "granted" ? onGranted() : setAsked(true)),
    onError: () => toast.error(t("consent.failed")),
  });
  return (
    <div className="flex flex-col items-start gap-2">
      <p className="text-sm text-muted-foreground">
        {asked ? t("consent.asked") : t("consent.needed")}
      </p>
      {!asked && (
        <Button variant="secondary" size="sm" pending={ask.isPending} onClick={() => ask.mutate()}>
          {t("consent.ask")}
        </Button>
      )}
    </div>
  );
}
