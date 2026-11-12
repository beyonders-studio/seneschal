import axios from "axios";
import { useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";

import { askFilesConsent, searchFiles } from "@/api/generated/files/files";
import type { FileFound } from "@/api/generated/hoard.schemas";
import { toast } from "@/components/Toaster";
import type { FileChoice, FileFieldFiles } from "@/ui/properties/FileFieldInput";

/** How long a reminder to answer Initiative's consent request stays told. */
const TOLD_MS = 5_000;

/** Whether Hoard refused a call until the member lets it read Initiative's
 *  files for them. */
export const needsConsent = (error: unknown): boolean =>
  axios.isAxiosError(error) && error.response?.data?.detail === "FILES_CONSENT_REQUIRED";

const choice = (file: FileFound): FileChoice => ({
  id: file.id,
  label: file.name,
  ...(file.initiative_name ? { sublabel: file.initiative_name } : {}),
});

/**
 * Initiative's files as the kit's file pickers find them: the ones the member
 * viewing the page may open. Until the member lets Hoard read them, a search
 * asks them in Initiative and finds nothing; once they have, it finds again.
 */
export function useInitiativeFiles(): FileFieldFiles {
  const { t } = useTranslation("documents");
  const toldAt = useRef(0);
  return useMemo(() => {
    const search = async (query: string) => (await searchFiles({ search: query })).map(choice);
    return {
      find: async (query) => {
        try {
          return await search(query);
        } catch (error) {
          if (!needsConsent(error)) throw error;
        }
        const { status } = await askFilesConsent();
        if (status === "granted") return search(query);
        if (Date.now() - toldAt.current > TOLD_MS) {
          toldAt.current = Date.now();
          toast.error(t("consent.asked"));
        }
        return [];
      },
      // Initiative's pages open in Initiative, which Hoard's frame doesn't navigate.
      href: () => "",
      preview: () => null,
      newFile: () => null,
    };
  }, [t]);
}
