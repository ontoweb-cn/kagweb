"use client";

import dynamic from "next/dynamic";

import { SettingsDomainGate } from "@/components/settings/SettingsDomainGate";

const SettingsSection = dynamic(
  () => import("@/features/settings/sections/ModelsSettingsSection"),
  { loading: () => <div className="min-h-80" aria-hidden="true" /> },
);

/**
 * 语言、搜索、语音与生成模型
 *
 * The section component is the same one the single settings document used to
 * stack inline; only its route changed. `SettingsDomainGate` supplies the
 * visibility check that document performed when it filtered its sections.
 */
export default function ModelsSettingsRoute() {
  return (
    <SettingsDomainGate domain="models">
      <SettingsSection />
    </SettingsDomainGate>
  );
}
