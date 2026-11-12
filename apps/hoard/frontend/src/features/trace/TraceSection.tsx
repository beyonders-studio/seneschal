import { Link } from "@tanstack/react-router";
import { Undo2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { LotRead } from "@/api/generated/hoard.schemas";
import { useReadLotTrace } from "@/api/generated/trace/trace";
import { StepsTable } from "@/features/trace/StepsTable";
import { errorMessage } from "@/lib/errors";
import { Button } from "@/ui/button";
import { StatusMessage } from "@/ui/status-message";

/** A lot's trace, on its page: back to what went into it, and forward to
 *  what was made from it and where it went, what may have gone apart. */
export function TraceSection({ lot }: { lot: LotRead }) {
  const { t } = useTranslation("trace");
  const { data, error, refetch } = useReadLotTrace(lot.id);

  if (error)
    return (
      <StatusMessage
        description={errorMessage(error, t, "section.loadError")}
        onRetry={() => void refetch()}
      />
    );
  if (!data) return <p className="text-sm text-muted-foreground">{t("hoard:common.loading")}</p>;

  const recorded = data.forward.filter((step) => !step.assumed);
  const assumed = data.forward.filter((step) => step.assumed);
  return (
    <div className="flex flex-col gap-4">
      {data.back.length === 0 && data.forward.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("section.empty")}</p>
      ) : (
        <>
          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">{t("section.back")}</h3>
            <StepsTable steps={data.back} empty={t("section.backEmpty")} />
          </section>
          <section className="flex flex-col gap-2">
            <h3 className="text-sm font-medium">{t("section.forward")}</h3>
            <StepsTable steps={recorded} empty={t("section.forwardEmpty")} />
          </section>
          {assumed.length > 0 && (
            <section className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">{t("section.mayHave")}</h3>
              <p className="text-sm text-muted-foreground">{t("section.mayHaveHint")}</p>
              <StepsTable steps={assumed} />
            </section>
          )}
        </>
      )}
      <div>
        <Button asChild variant="outline" size="sm">
          <Link to="/stock/recall" search={{ lot: lot.id }}>
            <Undo2 aria-hidden />
            {t("section.recall")}
          </Link>
        </Button>
      </div>
    </div>
  );
}
