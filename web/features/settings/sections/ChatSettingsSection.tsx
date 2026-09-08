"use client";

import dynamic from "next/dynamic";

import { CategoryScroll } from "@/components/settings/CategoryScroll";
import { useSettingsAccess } from "@/features/settings/navigation/SettingsAccessProvider";
import { visibleSettingsChildren } from "@/features/settings/navigation/settings-nav";

const loading = () => <div className="min-h-64" aria-hidden="true" />;
const AgentLoopSettingsPage = dynamic(
  () => import("./AgentLoopSettingsSection"),
  { loading },
);
const ToolsSettingsPage = dynamic(() => import("./ToolsSettingsSection"), {
  loading,
});
const CapabilitiesSettingsPage = dynamic(
  () => import("./CapabilitiesSettingsSection"),
  { loading },
);
const StarterSettingsPage = dynamic(
  () => import("./StartersSettingsSection"),
  { loading },
);
const AttachmentSettingsPage = dynamic(
  () => import("./AttachmentsSettingsSection"),
  { loading },
);

const CHAT_SECTIONS = [
  { key: "agent-loop", Component: AgentLoopSettingsPage },
  { key: "tools", Component: ToolsSettingsPage },
  { key: "capabilities", Component: CapabilitiesSettingsPage },
  { key: "starters", Component: StarterSettingsPage },
  { key: "attachments", Component: AttachmentSettingsPage },
] as const;

/**
 * The Chat category, in full — see `ModelsSettingsPage` for the pattern.
 */
export default function ChatSettingsPage() {
  const access = useSettingsAccess();
  const visibleKeys = new Set(
    visibleSettingsChildren("chat", access).map((leaf) => leaf.key),
  );
  return (
    <CategoryScroll
      sections={CHAT_SECTIONS.filter(({ key }) => visibleKeys.has(key))}
      deferSections
    />
  );
}
