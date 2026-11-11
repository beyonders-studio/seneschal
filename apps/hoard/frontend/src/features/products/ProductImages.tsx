import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  createProductImage,
  deleteProductImage,
  getListProductImagesQueryKey,
  useListProductImages,
} from "@/api/generated/products/products";
import { toast } from "@/components/Toaster";
import { errorMessage } from "@/lib/errors";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { FileDropArea } from "@/ui/file-drop";

/** The pictures Hoard keeps of a product (the backend's `IMAGE_TYPES`). */
const IMAGE_TYPES = ["image/gif", "image/jpeg", "image/png", "image/webp"];
/** The most one picture may hold (the backend's `MAX_IMAGE_BYTES`). */
const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

/** Where a product's image is read from. */
export const imageUrl = (productId: number, imageId: number) =>
  `/api/v1/products/${productId}/images/${imageId}`;

/** A product's pictures, first to last, and adding and removing them while
 *  Hoard may change. */
export function ProductImages({ productId, name }: { productId: number; name: string }) {
  const { t } = useTranslation("catalog");
  const { read_only } = useSession();
  const queryClient = useQueryClient();
  const images = useListProductImages(productId).data ?? [];
  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: getListProductImagesQueryKey(productId) });
  const add = useMutation({
    mutationFn: (file: File) =>
      createProductImage(productId, file, { headers: { "Content-Type": file.type } }),
    onSuccess: refresh,
    onError: (error) => toast.error(errorMessage(error, t, "products.imageFailed")),
  });
  const remove = useMutation({
    mutationFn: (imageId: number) => deleteProductImage(productId, imageId),
    onSuccess: refresh,
    onError: (error) => toast.error(errorMessage(error, t, "products.imageFailed")),
  });

  const addFile = (file: File) => {
    if (!IMAGE_TYPES.includes(file.type)) return toast.error(t("errors.IMAGE_TYPE"));
    if (file.size > MAX_IMAGE_BYTES) return toast.error(t("errors.IMAGE_TOO_LARGE"));
    add.mutate(file);
  };

  return (
    <div className="flex flex-col gap-3">
      {images.length === 0 && read_only && (
        <p className="text-sm text-muted-foreground">{t("products.noImages")}</p>
      )}
      {images.length > 0 && (
        <ul className="grid grid-cols-fill-32/6 gap-3">
          {images.map((image, index) => (
            <li key={image.id} className="relative overflow-hidden rounded-md border">
              <img
                src={imageUrl(productId, image.id)}
                alt={t("products.imageOf", { name, number: index + 1 })}
                className="aspect-square w-full object-cover"
              />
              {!read_only && (
                <Button
                  variant="secondary"
                  size="icon-sm"
                  className="absolute top-1 right-1"
                  aria-label={t("products.removeImage", { number: index + 1 })}
                  disabled={remove.isPending}
                  onClick={() => remove.mutate(image.id)}
                >
                  <Trash2 aria-hidden />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {!read_only && (
        <FileDropArea
          accept={IMAGE_TYPES.join(",")}
          onFile={addFile}
          prompt={t("products.imagePrompt")}
          disabled={add.isPending}
        />
      )}
    </div>
  );
}
