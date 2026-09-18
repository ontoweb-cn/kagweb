"use client";

import dynamic from "next/dynamic";

import { SettingsDomainGate } from "@/components/settings/SettingsDomainGate";

const SettingsSection = dynamic(
  () => import("@/features/settings/sections/KagSettingsSection"),
  { loading: () => <div className="min-h-80" aria-hidden="true" /> },
);

/**
 * KAG 集成设置（admin-only）。
 *
 * 与 agent-loop 路由同构：域门禁（SettingsDomainGate）+ 动态加载区块，
 * 仅 route 与挂载方式随一级分类模型落地。
 */
export default function KagSettingsRoute() {
  return (
    <SettingsDomainGate domain="kag">
      <SettingsSection />
    </SettingsDomainGate>
  );
}
