"use client";

import {
  AudioLines,
  Bot,
  Boxes,
  Brain,
  Image as ImageIcon,
  Info,
  KeyRound,
  Library,
  ListChecks,
  MessagesSquare,
  Mic,
  Network,
  Palette,
  Paperclip,
  Search,
  SlidersHorizontal,
  Sparkles,
  Video,
  type LucideIcon,
} from "lucide-react";

import type { ServiceName } from "@/features/settings/store/SettingsStore";
import type { SettingsAccess } from "@/features/settings/navigation/settings-access";

/**
 * Settings information architecture.
 *
 * Every first-level category is a real route (`/settings/models`), and the
 * leaves inside a category are anchors within that route
 * (`/settings/models#llm`). This module is the single source for those URLs,
 * plus labels, visibility, search metadata, and the storage-path hint the
 * toolbar shows.
 *
 * No `href` field is stored on a category or leaf: a hand-written href sitting
 * next to a key is a second source of truth that drifts, and it did — the
 * knowledge section shipped a `/settings#document-parsing` link while the
 * section it pointed at rendered `id="knowledge"`. Every URL is derived from
 * the key by `settingsHref` instead.
 */

export type Lang = { zh: string; en: string };

export interface SettingsLeaf {
  key: string;
  label: Lang;
  blurb: Lang;
  icon: LucideIcon;
  /** Colored icon-tile accent for the sub-hub grid (full class strings). */
  tile: string;
  /** Model-service leaves carry a configured/not chip from the catalog. */
  service?: ServiceName;
  /** Hidden from non-admin users (the backend rejects them anyway). */
  adminOnly?: boolean;
  /**
   * Shown only when the conversation-facing LLM settings apply to the
   * configured agent backend (see ``SettingsAccess.enableLlmSettings``).
   * Leaf-level, not category-level: a CLI backend supplies its own
   * conversation models but still relies on this category's task service for
   * KAGWeb's own calls and its voice services for /api/voice, so hiding the
   * whole category would bury the very settings the startup warning points
   * at.
   */
  llmOnly?: boolean;
}

export interface SettingsCategory {
  key: string;
  label: Lang;
  /** One-line descriptor shown on the hub block. */
  blurb: Lang;
  icon: LucideIcon;
  /** Nested anchors (omitted for direct-section categories). */
  children?: SettingsLeaf[];
  /** Admin-owned category, hidden from ordinary users like an adminOnly leaf. */
  adminOnly?: boolean;
}

export function isSettingsLeafVisible(
  leaf: SettingsLeaf,
  access: SettingsAccess,
): boolean {
  if (leaf.adminOnly && access.hideAdminOnly) return false;
  if (leaf.llmOnly && !access.enableLlmSettings) return false;
  return true;
}

export function isSettingsCategoryVisible(
  category: SettingsCategory,
  access: SettingsAccess,
): boolean {
  if (category.adminOnly && access.hideAdminOnly) return false;
  return (
    !category.children ||
    category.children.some((leaf) => isSettingsLeafVisible(leaf, access))
  );
}

export function visibleSettingsChildren(
  categoryKey: string,
  access: SettingsAccess,
): SettingsLeaf[] {
  return (
    SETTINGS_CATEGORIES.find((category) => category.key === categoryKey)
      ?.children ?? []
  ).filter((leaf) => isSettingsLeafVisible(leaf, access));
}

/**
 * Why a category route may not render for this user, or `null` when it may.
 *
 * The single page used to enforce this by filtering the sections it stacked
 * into the document. With one route per category that filter is gone, so every
 * category route has to ask — otherwise an ordinary user could open
 * `/settings/agent-loop` directly and see an admin-only page.
 *
 * Only admin ownership can block a category. Backend applicability is a
 * leaf-level concern: a CLI backend hides the conversation-LLM leaf but keeps
 * the category, because the task service (KAGWeb's own calls) and the voice
 * services apply no matter which backend drives conversations.
 */
export type SettingsDomainBlock = "admin-only";

export function settingsDomainBlockReason(
  domainKey: string,
  access: SettingsAccess,
): SettingsDomainBlock | null {
  const category = SETTINGS_CATEGORIES.find((item) => item.key === domainKey);
  // An unknown domain is not a page this build knows how to authorize.
  if (!category) return "admin-only";
  if (category.adminOnly && access.hideAdminOnly) return "admin-only";
  if (
    category.children &&
    !category.children.some((leaf) => isSettingsLeafVisible(leaf, access))
  ) {
    return "admin-only";
  }
  return null;
}

const MODEL_CHILDREN: SettingsLeaf[] = [
  {
    key: "connections",
    label: { zh: "连接", en: "Connections" },
    blurb: {
      zh: "一份凭据供给多个服务。",
      en: "One credential, supplying several services.",
    },
    icon: KeyRound,
    tile: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
  },
  {
    // The conversation-facing LLM profiles. Hidden when the configured agent
    // backend supplies its own conversation models (the CLI family); the rest
    // of this category stays visible because task/voice services apply
    // regardless of backend.
    key: "llm",
    llmOnly: true,
    label: { zh: "LLM", en: "LLM" },
    blurb: {
      zh: "语言模型供应商与当前档位。",
      en: "Language model providers and active profile.",
    },
    icon: Brain,
    tile: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
    service: "llm",
  },
  {
    key: "task-models",
    // The model behind KAGWeb's own calls — session titles, turn insights,
    // history summaries. Resolved in-process from this catalog no matter
    // which agent backend drives conversations, which is why this leaf — not
    // the whole category — is what the startup warning's "Settings > Models"
    // advice points at.
    label: { zh: "任务模型", en: "Task models" },
    blurb: {
      zh: "KAGWeb 自己发起的调用使用的模型。",
      en: "The model behind the calls KAGWeb makes on its own.",
    },
    icon: ListChecks,
    tile: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400",
    service: "task",
  },
  {
    key: "search",
    label: { zh: "搜索", en: "Search" },
    blurb: { zh: "联网搜索供应商。", en: "Web search providers." },
    icon: Search,
    tile: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
    service: "search",
  },
  {
    key: "tts",
    label: { zh: "语音合成", en: "Text-to-Speech" },
    blurb: {
      zh: "朗读助手回复的 TTS 供应商。",
      en: "Text-to-speech for reading replies aloud.",
    },
    icon: AudioLines,
    tile: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
    service: "tts",
  },
  {
    key: "stt",
    label: { zh: "语音识别", en: "Speech-to-Text" },
    blurb: {
      zh: "转写麦克风录音的 STT 供应商。",
      en: "Speech-to-text for the composer microphone.",
    },
    icon: Mic,
    tile: "bg-pink-500/10 text-pink-600 dark:text-pink-400",
    service: "stt",
  },
  {
    key: "imagegen",
    label: { zh: "文生图", en: "Image Generation" },
    blurb: {
      zh: "chat imagegen 工具使用的文生图模型。",
      en: "Text-to-image model for the chat imagegen tool.",
    },
    icon: ImageIcon,
    tile: "bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400",
    service: "imagegen",
  },
  {
    // This leaf was missing while the section it belongs to kept rendering
    // (ModelsSettingsSection stacks it), so the connection editor's videogen
    // chip had nothing to link at and the navigator never listed the page.
    key: "videogen",
    label: { zh: "文生视频", en: "Video Generation" },
    blurb: {
      zh: "chat videogen 工具使用的文生视频模型。",
      en: "Text-to-video model for the chat videogen tool.",
    },
    icon: Video,
    tile: "bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
    service: "videogen",
  },
];

const CHAT_CHILDREN: SettingsLeaf[] = [
  {
    key: "capabilities",
    label: { zh: "能力", en: "Capabilities" },
    blurb: {
      zh: "各能力的 LLM 参数与运行时旋钮。",
      en: "Per-capability LLM parameters and runtime knobs.",
    },
    icon: SlidersHorizontal,
    tile: "bg-lime-500/10 text-lime-600 dark:text-lime-400",
  },
  {
    key: "starters",
    label: { zh: "起始建议", en: "Starting points" },
    blurb: {
      zh: "主页输入框下方那三行引导的素材范围。",
      en: "How much history shapes the three lines under the composer.",
    },
    icon: Sparkles,
    tile: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
  },
  {
    key: "attachments",
    label: { zh: "附件", en: "Attachments" },
    blurb: {
      zh: "聊天附件的大小上限与文本提取预算。",
      en: "Upload caps and extraction budgets for chat attachments.",
    },
    icon: Paperclip,
    tile: "bg-teal-500/10 text-teal-600 dark:text-teal-400",
    adminOnly: true,
  },
];

export const SETTINGS_CATEGORIES: SettingsCategory[] = [
  {
    key: "appearance",
    label: { zh: "外观", en: "Appearance" },
    blurb: { zh: "视觉主题与界面语言", en: "Theme and interface language" },
    icon: Palette,
  },
  {
    key: "network",
    label: { zh: "网络", en: "Network" },
    blurb: {
      zh: "端口、浏览器 API 地址与 CORS",
      en: "Ports, browser API base, and CORS",
    },
    icon: Network,
  },
  {
    // The key doubles as the route segment, so it stays `agent-loop` even
    // though the category is presented as "Agent Backend" — renaming it would
    // break the stored storage-path mapping and the recorded deep links.
    key: "agent-loop",
    adminOnly: true,
    label: { zh: "智能体后端", en: "Agent Backend" },
    blurb: {
      zh: "选择驱动对话的智能体后端（Intellect、HERMES、AgentScope…）。",
      en: "The agent backend that drives conversations.",
    },
    icon: Bot,
  },
  {
    // Always visible: which leaves render depends on the backend (the llm
    // leaf hides under CLI backends), but the task and voice services here
    // apply no matter which backend drives conversations.
    key: "models",
    label: { zh: "模型", en: "Models" },
    blurb: {
      zh: "语言、搜索、语音与生成模型",
      en: "Language, search, voice, and generation models",
    },
    icon: Boxes,
    children: MODEL_CHILDREN,
  },
  {
    key: "knowledge",
    label: { zh: "知识库", en: "Knowledge Base" },
    blurb: { zh: "文档解析引擎", en: "Document parsing engine" },
    icon: Library,
  },
  {
    key: "chat",
    label: { zh: "聊天", en: "Chat" },
    blurb: {
      zh: "Agent Loop、工具、能力与附件",
      en: "Agent Loop, tools, capabilities, and attachments",
    },
    icon: MessagesSquare,
    children: CHAT_CHILDREN,
  },
  {
    key: "about",
    label: { zh: "关于", en: "About" },
    blurb: {
      zh: "版本、更新与项目资源",
      en: "Version, updates, and project resources",
    },
    icon: Info,
  },
];

/** The settings index. */
export const SETTINGS_HUB_HREF = "/settings";

/**
 * Category key → route.
 *
 * A category missing from this map is a wiring error: `settingsHref` would
 * silently fall back to the hub, so the node tests assert that every category
 * key and every leaf key resolves to a real URL.
 */
export const SETTINGS_ROUTES: Record<string, string> = {
  appearance: "/settings/appearance",
  network: "/settings/network",
  "agent-loop": "/settings/agent-loop",
  models: "/settings/models",
  knowledge: "/settings/knowledge",
  chat: "/settings/chat",
  about: "/settings/about",
};

/** Leaf key → the category route that contains it. */
const LEAF_DOMAIN: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const category of SETTINGS_CATEGORIES) {
    for (const leaf of category.children ?? []) map[leaf.key] = category.key;
  }
  return map;
})();

/**
 * Canonical URL for a settings section key.
 *
 * `overview` is the index; a category key is its own route; a leaf key is its
 * category's route plus the leaf's in-document anchor.
 */
export function settingsHref(key: string): string {
  if (key === "overview") return SETTINGS_HUB_HREF;
  const domain = LEAF_DOMAIN[key];
  if (domain) return `${SETTINGS_ROUTES[domain] ?? SETTINGS_HUB_HREF}#${key}`;
  return SETTINGS_ROUTES[key] ?? SETTINGS_HUB_HREF;
}

/** The category key a route belongs to, or null when the path is not settings. */
export function settingsDomainForRoute(pathname: string): string | null {
  return (
    Object.entries(SETTINGS_ROUTES).find(
      ([, route]) => route === pathname,
    )?.[0] ?? null
  );
}

// The on-disk file (under data/user/settings/) each section persists to.
// Surfaced in the toolbar status line so every page says where its parameters
// live. Keyed by section key rather than by URL, which is what the navigator
// and the scroll tracker already agree on, so a route change cannot leave a
// page pointing at a stale path.
const STORAGE_PATHS: Record<string, string> = {
  appearance: "data/user/settings/interface.json",
  network: "data/user/settings/system.json",
  "agent-loop": "data/user/settings/system.json",
  knowledge: "data/user/settings/document_parsing.json",
  connections: "data/user/settings/model_catalog.json",
  llm: "data/user/settings/model_catalog.json",
  "task-models": "data/user/settings/model_catalog.json",
  search: "data/user/settings/model_catalog.json",
  tts: "data/user/settings/model_catalog.json",
  stt: "data/user/settings/model_catalog.json",
  imagegen: "data/user/settings/model_catalog.json",
  videogen: "data/user/settings/model_catalog.json",
  // Only added where every leaf of the category shares one file. `chat` is
  // deliberately absent: its leaves span main.yaml, interface.json and
  // system.json, so a category-level guess would name the wrong file and a
  // wrong path is worse than no path.
  models: "data/user/settings/model_catalog.json",
  capabilities: "data/user/settings/main.yaml",
  starters: "data/user/settings/interface.json",
  attachments: "data/user/settings/system.json",
};

export function storagePathFor(
  pathname: string,
  activeSection?: string | null,
): string | null {
  if (pathname === SETTINGS_HUB_HREF) {
    return activeSection ? (STORAGE_PATHS[activeSection] ?? null) : null;
  }
  const domain = settingsDomainForRoute(pathname);
  if (!domain) return null;
  return STORAGE_PATHS[activeSection ?? domain] ?? null;
}
