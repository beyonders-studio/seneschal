import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useListSetups } from "@/api/generated/setups/setups";
import { useChangesSettings } from "@/features/settings/NamedList";
import { useAddingSetup } from "@/features/setups/adding";
import { useSession } from "@/session";
import { Button } from "@/ui/button";

/** Where this browser remembers that an admin chose to start blank. */
const dismissedKey = (community: string) => `hoard.setupSuggestion.dismissed.${community}`;

function remembered(key: string): boolean {
  try {
    return window.localStorage.getItem(key) === "1";
  } catch {
    return false;
  }
}

function remember(key: string) {
  try {
    window.localStorage.setItem(key, "1");
  } catch {
    // Not remembered: it is suggested again next time.
  }
}

/** A starting setup, suggested to an admin while the organization has not
 *  begun, on every page but Settings, which lists the setups itself: adding
 *  one, or starting blank, puts it away. Nothing asks for one. */
export function SetupSuggestion() {
  const { t } = useTranslation("setups");
  const { community_ref, page } = useSession();
  const changes = useChangesSettings() && page !== "settings";
  const key = dismissedKey(community_ref);
  const [dismissed, setDismissed] = useState(() => remembered(key));
  const offered = useListSetups({ query: { enabled: changes && !dismissed } }).data;
  const adding = useAddingSetup();
  if (!changes || dismissed || !offered || offered.started) return null;
  if (offered.setups.some((setup) => setup.added)) return null;
  const startBlank = () => {
    remember(key);
    setDismissed(true);
  };
  return (
    <section
      aria-labelledby="setup-suggestion"
      className="flex flex-col gap-3 rounded-lg border bg-card p-4"
    >
      <h2 id="setup-suggestion" className="font-semibold">
        {t("suggestion.title")}
      </h2>
      <p className="text-sm text-muted-foreground">{t("suggestion.body")}</p>
      <ul className="grid grid-cols-fill-60/3 gap-3">
        {offered.setups.map((setup) => (
          <li key={setup.key}>
            <button
              type="button"
              disabled={adding.isPending}
              onClick={() => adding.mutate(setup)}
              className="flex h-full w-full flex-col gap-1 rounded-md border p-3 text-start hover:bg-accent disabled:opacity-50"
            >
              <span className="font-medium">{t(`setups.${setup.key}.name`)}</span>
              <span className="text-sm text-muted-foreground">
                {t(`setups.${setup.key}.description`)}
              </span>
            </button>
          </li>
        ))}
      </ul>
      <div>
        <Button variant="ghost" size="sm" onClick={startBlank}>
          {t("suggestion.blank")}
        </Button>
      </div>
    </section>
  );
}
