import { BrainCircuit, MessageSquare, type LucideIcon } from "lucide-react";

import type { CapabilityDescriptor } from "./model";

export interface CapabilityDef {
  value: string;
  label: string;
  description: string;
  icon: LucideIcon;
  secondary?: boolean;
}

/**
 * Curated presentation for the built-in catalog. Identity and availability
 * are backend-owned (``/api/capabilities/registered``); this list only adds
 * label/icon metadata for ids it knows about.
 */
export const CHAT_CAPABILITIES: CapabilityDef[] = [
  {
    value: "",
    label: "Chat",
    description: "Flexible conversation, handled by the agent backend",
    icon: MessageSquare,
  },
];

/** Removed capability ids that must never render, even if a backend still
 *  offers them (e.g. an out-of-tree plugin registering a pre-shutdown id). */
const HIDDEN_CAPABILITY_IDS = new Set([
  "course_study",
  "immersive_reading",
  "mastery_path",
]);

export function getChatCapability(value: string | null): CapabilityDef {
  return (
    CHAT_CAPABILITIES.find(
      (capability) => capability.value === (value || ""),
    ) ?? CHAT_CAPABILITIES[0]
  );
}

const UNKNOWN_PRESENTATION: Omit<CapabilityDef, "value" | "label" | "description"> =
  {
    icon: BrainCircuit,
    secondary: true,
  };

function humanizeCapabilityId(id: string): string {
  return id
    .split(/[_-]+/)
    .filter(Boolean)
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

export function mergeCapabilityPresentations(
  capabilities: readonly CapabilityDescriptor[],
): CapabilityDef[] {
  const byId = new Map(
    CHAT_CAPABILITIES.map((entry) => [entry.value || "chat", entry] as const),
  );

  return capabilities
    .filter((capability) => capability.available)
    .map((capability) => {
      const known = byId.get(capability.id);
      if (known) {
        return { ...known };
      }
      const label =
        typeof capability.manifest?.name === "string" &&
        capability.manifest.name.trim()
          ? capability.manifest.name.trim()
          : humanizeCapabilityId(capability.id);
      return {
        ...UNKNOWN_PRESENTATION,
        value: capability.id,
        label,
        description:
          typeof capability.manifest?.description === "string"
            ? capability.manifest.description
            : "Extension capability",
      };
    });
}

export function visibleCapabilityPresentations(
  capabilities: readonly CapabilityDef[],
): CapabilityDef[] {
  const catalogOrder = new Map(
    CHAT_CAPABILITIES.map((capability, index) => [capability.value, index]),
  );

  return capabilities
    .filter(
      (capability) => !HIDDEN_CAPABILITY_IDS.has(capability.value),
    )
    .map((capability, sourceIndex) => ({ capability, sourceIndex }))
    .sort((left, right) => {
      const leftOrder = catalogOrder.get(left.capability.value);
      const rightOrder = catalogOrder.get(right.capability.value);
      if (leftOrder === undefined && rightOrder === undefined) {
        return left.sourceIndex - right.sourceIndex;
      }
      if (leftOrder === undefined) return 1;
      if (rightOrder === undefined) return -1;
      return leftOrder - rightOrder;
    })
    .map(({ capability }) => capability);
}
