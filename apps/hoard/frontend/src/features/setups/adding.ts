import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import type { SetupRead } from "@/api/generated/hoard.schemas";
import { addSetup } from "@/api/generated/setups/setups";
import { toast } from "@/components/Toaster";
import { errorMessage } from "@/lib/errors";

/** Adds a starting setup in the reader's words: each of its keys as the
 *  `setups.<setup>.<key>` words name it. Everything a setup adds shows
 *  elsewhere, so every read is refreshed after. */
export function useAddingSetup() {
  const { t } = useTranslation("setups");
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (setup: SetupRead) =>
      addSetup(setup.key, {
        words: Object.fromEntries(
          setup.words.map((word) => [word, t(`setups.${setup.key}.${word}`)])
        ),
      }),
    onSuccess: async (setup) => {
      await queryClient.invalidateQueries();
      toast.success(t("section.addedToast", { name: t(`setups.${setup.key}.name`) }));
    },
    onError: (error) => toast.error(errorMessage(error, t, "section.error")),
  });
}
