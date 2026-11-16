import { useMutation } from "@tanstack/react-query";
import type { TFunction } from "i18next";
import { Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { deleteItemPicture, setItemPicture } from "@/api/generated/items/items";
import { toast } from "@/components/Toaster";
import { errorMessage } from "@/lib/errors";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { FileDropArea } from "@/ui/file-drop";

/** The pictures Hoard keeps (the backend's `IMAGE_TYPES`). */
export const IMAGE_TYPES = ["image/gif", "image/jpeg", "image/png", "image/webp"];
/** The most one picture may hold (the backend's `MAX_IMAGE_BYTES`). */
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

/** Why Hoard won't keep `file` as a picture, in the reader's words; null when it will. */
export const pictureRefused = (file: File, t: TFunction): string | null => {
  if (!IMAGE_TYPES.includes(file.type)) return t("catalog:errors.IMAGE_TYPE");
  if (file.size > MAX_IMAGE_BYTES) return t("catalog:errors.IMAGE_TOO_LARGE");
  return null;
};

/** Where a material's or a base's picture is read from. */
export const pictureUrl = (itemId: number, pictureId: number) =>
  `/api/v1/items/${itemId}/picture/${pictureId}`;

/** Keep `file` as a material's or a base's picture, in place of any it had. */
const savePicture = (itemId: number, file: File) =>
  setItemPicture(itemId, file, { headers: { "Content-Type": file.type } });

/** A material or base just added, pictured by `file`. One whose picture
 *  isn't kept is still added, and says so. */
export async function withPicture<T extends { id: number; picture_id: number | null }>(
  item: T,
  file: File,
  t: TFunction
): Promise<T> {
  try {
    return { ...item, picture_id: (await savePicture(item.id, file)).id };
  } catch (error) {
    toast.error(errorMessage(error, t, "catalog:items.pictureFailed"));
    return item;
  }
}

/** A material's or a base's one picture, replaced and removed here while
 *  Hoard may change. */
export function ItemPicture({
  item,
  onChanged,
}: {
  item: { id: number; name: string; picture_id: number | null };
  onChanged: () => void;
}) {
  const { t } = useTranslation("catalog");
  const { read_only } = useSession();
  const failed = (error: unknown) => toast.error(errorMessage(error, t, "items.pictureFailed"));
  const replace = useMutation({
    mutationFn: (file: File) => savePicture(item.id, file),
    onSuccess: onChanged,
    onError: failed,
  });
  const remove = useMutation({
    mutationFn: () => deleteItemPicture(item.id),
    onSuccess: onChanged,
    onError: failed,
  });
  const addFile = (file: File) => {
    const refused = pictureRefused(file, t);
    if (refused) return toast.error(refused);
    replace.mutate(file);
  };

  return (
    <div className="flex flex-col gap-3">
      {item.picture_id === null && read_only && (
        <p className="text-sm text-muted-foreground">{t("items.noPicture")}</p>
      )}
      {item.picture_id !== null && (
        <div className="relative w-48 overflow-hidden rounded-md border">
          <img
            src={pictureUrl(item.id, item.picture_id)}
            alt={t("items.pictureOf", { name: item.name })}
            className="aspect-square w-full object-cover"
          />
          {!read_only && (
            <Button
              variant="secondary"
              size="icon-sm"
              className="absolute top-1 right-1"
              aria-label={t("items.removePicture")}
              disabled={remove.isPending}
              onClick={() => remove.mutate()}
            >
              <Trash2 aria-hidden />
            </Button>
          )}
        </div>
      )}
      {!read_only && (
        <FileDropArea
          accept={IMAGE_TYPES.join(",")}
          onFile={addFile}
          prompt={t(item.picture_id === null ? "items.picturePrompt" : "items.pictureReplace")}
          disabled={replace.isPending}
        />
      )}
    </div>
  );
}
