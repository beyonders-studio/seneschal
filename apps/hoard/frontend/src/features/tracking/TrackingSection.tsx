import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  getReadTrackingQueryKey,
  sendTrackingEvents,
  useReadTracking,
} from "@/api/generated/tracking/tracking";
import { toast } from "@/components/Toaster";
import { useChangesSettings } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { errorMessage } from "@/lib/errors";
import { Button } from "@/ui/button";
import { dateTimeFormat } from "@/ui/lib/intl";

/**
 * How runs and purchases are tracked on Initiative's tasks: how many are,
 * the events still waiting to reach Initiative, and, for an admin, sending
 * them now.
 */
export function TrackingSection() {
  const { t, i18n } = useTranslation("tracking");
  const tracking = useReadTracking().data;
  const changes = useChangesSettings();
  const queryClient = useQueryClient();
  const send = useMutation({
    mutationFn: () => sendTrackingEvents(),
    onSuccess: (answer) => {
      queryClient.setQueryData(getReadTrackingQueryKey(), answer);
      if (answer.waiting) toast.error(t("send.waiting"));
      else toast.success(t("send.sent"));
    },
    onError: (error) => toast.error(errorMessage(error, t, "send.failed")),
  });
  const retryAt = tracking?.retry_at
    ? dateTimeFormat(i18n.resolvedLanguage, { dateStyle: "medium", timeStyle: "short" }).format(
        new Date(tracking.retry_at)
      )
    : null;
  return (
    <SettingsSection id="tracking" title={t("section.title")}>
      <p className="text-sm text-muted-foreground">{t("section.description")}</p>
      {tracking && (
        <>
          <p className="text-sm">
            {t("tracked", {
              runs: t("runs", { count: tracking.runs }),
              purchases: t("purchases", { count: tracking.purchases }),
            })}
          </p>
          <p className="text-sm">
            {tracking.waiting ? t("waiting", { count: tracking.waiting }) : t("allSent")}
            {retryAt && ` ${t("retry", { time: retryAt })}`}
          </p>
          {changes && tracking.waiting > 0 && (
            <div>
              <Button variant="secondary" pending={send.isPending} onClick={() => send.mutate()}>
                {t("send.action")}
              </Button>
            </div>
          )}
        </>
      )}
    </SettingsSection>
  );
}
