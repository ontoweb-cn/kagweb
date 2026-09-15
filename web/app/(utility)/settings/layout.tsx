import SettingsMain from "@/components/settings/SettingsMain";
import {
  ModelCatalogProvider,
  SettingsDraftProvider,
  SettingsProvider,
  UiSettingsProvider,
} from "@/features/settings/store";
import { SettingsAccessProvider } from "@/features/settings/navigation/SettingsAccessProvider";
import { SettingsLegacyAnchorRedirect } from "@/components/settings/SettingsLegacyAnchorRedirect";
import { SettingsTourOverlay } from "@/components/settings/SettingsTourOverlay";

export default function SettingsLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <SettingsProvider>
      <SettingsAccessProvider>
        <UiSettingsProvider>
          <ModelCatalogProvider>
            <SettingsDraftProvider>
              {/* The providers above sit on the layout, not on a page, so
                  moving between category routes keeps unsaved edits and the
                  loaded catalog instead of refetching them per route. */}
              <SettingsMain>{children}</SettingsMain>
              <SettingsLegacyAnchorRedirect />
              {/* Mounted once at the layout level so the cross-route guided tour
                  survives navigation between the hub and its sub-pages. */}
              <SettingsTourOverlay />
            </SettingsDraftProvider>
          </ModelCatalogProvider>
        </UiSettingsProvider>
      </SettingsAccessProvider>
    </SettingsProvider>
  );
}
