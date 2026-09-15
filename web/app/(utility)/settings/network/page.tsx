"use client";

import dynamic from "next/dynamic";

import { SettingsDomainGate } from "@/components/settings/SettingsDomainGate";

const SettingsSection = dynamic(
  () => import("@/features/settings/sections/NetworkSettingsSection"),
  { loading: () => <div className="min-h-80" aria-hidden="true" /> },
);

/**
 * 端口、API 地址与 CORS
 *
 * The section component is the same one the single settings document used to
 * stack inline; only its route changed. `SettingsDomainGate` supplies the
 * visibility check that document performed when it filtered its sections.
 */
export default function NetworkSettingsRoute() {
  return (
    <SettingsDomainGate domain="network">
      <SettingsSection />
    </SettingsDomainGate>
  );
}
