"use client";

import dynamic from "next/dynamic";

import { SettingsDomainGate } from "@/components/settings/SettingsDomainGate";

const SettingsSection = dynamic(
  () => import("@/features/settings/sections/AgentLoopSettingsSection"),
  { loading: () => <div className="min-h-80" aria-hidden="true" /> },
);

/**
 * 驱动对话的智能体后端
 *
 * The section component is the same one the single settings document used to
 * stack inline; only its route changed. `SettingsDomainGate` supplies the
 * visibility check that document performed when it filtered its sections.
 */
export default function AgentLoopSettingsRoute() {
  return (
    <SettingsDomainGate domain="agent-loop">
      <SettingsSection />
    </SettingsDomainGate>
  );
}
