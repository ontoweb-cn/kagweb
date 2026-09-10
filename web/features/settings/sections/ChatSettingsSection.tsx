"use client";

import dynamic from "next/dynamic";

import { CategoryScroll } from "@/components/settings/CategoryScroll";
import { useSettingsAccess } from "@/features/settings/navigation/SettingsAccessProvider";
import { visibleSettingsChildren } from "@/features/settings/navigation/settings-nav";

const loading = () => <div className="min-h-64" aria-hidden="true" />;
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

// The agent backend is a deployment-level choice, so it is its own top-level
// category now (see SETTINGS_CATEGORIES) rather than a child of Chat.
const CHAT_SECTIONS = [
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
