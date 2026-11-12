import { useTranslation } from "react-i18next";

import {
  createHoldReason,
  deleteHoldReason,
  getListHoldReasonsQueryKey,
  updateHoldReason,
  useListHoldReasons,
} from "@/api/generated/holds/holds";
import { useHoldReasonName } from "@/features/holds/reasons";
import { NamedList } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";

/** The reasons a lot is put on hold: the default ones, which people rename or
 *  remove, and their own. */
export function HoldReasonsSection() {
  const { t } = useTranslation("holds");
  return (
    <SettingsSection id="hold-reasons" title={t("reasons.title")}>
      <NamedList
        words="holdReasons"
        items={useListHoldReasons().data}
        queryKey={getListHoldReasonsQueryKey()}
        nameOf={useHoldReasonName()}
        add={(name) => createHoldReason({ name })}
        rename={(reason, name) => updateHoldReason(reason.id, { name })}
        remove={(reason) => deleteHoldReason(reason.id)}
      />
    </SettingsSection>
  );
}
