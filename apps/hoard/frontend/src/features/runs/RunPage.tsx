import { useTranslation } from "react-i18next";

/** One production run, under Production. */
export function RunPage({ runId }: { runId: number }) {
  const { t } = useTranslation("production");
  return <h2 className="text-xl font-semibold tracking-tight">{t("runs.pageTitle")}</h2>;
}
