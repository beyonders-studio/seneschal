import { createFileRoute } from "@tanstack/react-router";
import { useTranslation } from "react-i18next";

import { HoardPage } from "@/components/HoardPage";
import { AutoManufactureField } from "@/features/automanufacture/AutoManufactureField";
import { BackupSection } from "@/features/settings/BackupSection";
import { CategoriesSection } from "@/features/settings/CategoriesSection";
import { CurrencySection } from "@/features/settings/CurrencySection";
import { HoldReasonsSection } from "@/features/settings/HoldReasonsSection";
import { LocationsSection } from "@/features/settings/LocationsSection";
import { PropertiesSection } from "@/features/settings/PropertiesSection";
import { ReasonsSection } from "@/features/settings/ReasonsSection";
import { UnitsSection } from "@/features/settings/UnitsSection";
import { StoresSection } from "@/features/stores/StoresSection";
import { TrackingSection } from "@/features/tracking/TrackingSection";

/** Settings, opened at community level, which Initiative opens only to the
 *  community's admins. */
export const Route = createFileRoute("/settings")({ component: SettingsPage });

function SettingsPage() {
  const { t } = useTranslation();
  return (
    <HoardPage title={t("settings.title")}>
      <div className="flex flex-col gap-8">
        <CurrencySection />
        <UnitsSection />
        <CategoriesSection />
        <LocationsSection />
        <ReasonsSection />
        <HoldReasonsSection />
        <PropertiesSection />
        <TrackingSection />
        <StoresSection />
        <AutoManufactureField />
        <BackupSection />
      </div>
    </HoardPage>
  );
}
