"use client";

import {
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
} from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowUp,
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  MessageSquare,
  Mic,
  Paperclip,
  Plus,
  Sparkles,
  Square,
  X,
} from "lucide-react";
import {
  ATTACHMENT_ACCEPT,
  docIconFor,
  formatBytes,
  isSvgFilename,
} from "@/lib/doc-attachments";
import { useTranslation } from "react-i18next";

import type { SelectedHistorySession } from "@/components/chat/HistorySessionPicker";
import type { LLMSelection } from "@/features/chat/model/protocol";
import type { LLMOption } from "@/lib/llm-options";
import ChatSpaceMenu from "@/components/chat/space/ChatSpaceMenu";
import ContextBudgetChip, { type ContextBudget } from "./ContextBudgetChip";
import ModelSelector from "./ModelSelector";

type SpaceSelectionCounts = {
  attachments: number;
  chatHistory: number;
};
import ContextReferenceTree, {
  type ContextTreeItem,
} from "./ContextReferenceTree";
import { ComposerInput, type ComposerInputHandle } from "./ComposerInput";
import { useVoiceRecorder } from "@/hooks/useVoiceRecorder";
import type { CapabilityDef } from "@/features/capabilities/presentation";

interface PendingAttachment {
  type: string;
  filename: string;
  base64?: string;
  previewUrl?: string;
  size?: number;
  mimeType?: string;
}

/** One row in the capability picker — shared by the built-in list and the
 *  "More" flyout so both render identically. */
function CapMenuItem({
  cap,
  selected,
  onSelect,
}: {
  cap: CapabilityDef;
  selected: boolean;
  onSelect: (value: string) => void;
}) {
  const { t } = useTranslation();
  const Icon = cap.icon;
  return (
    <button
      type="button"
      onClick={() => onSelect(cap.value)}
      className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left transition-colors active:bg-[var(--muted)]/70 ${
        selected ? "bg-[var(--primary)]/[0.06]" : "hover:bg-[var(--muted)]/45"
      }`}
    >
      <Icon
        size={15}
        strokeWidth={1.7}
        className={`shrink-0 ${selected ? "text-[var(--primary)]" : "text-[var(--muted-foreground)]"}`}
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12.5px] font-medium leading-snug text-[var(--foreground)]">
          {t(cap.label)}
        </div>
        <div className="truncate text-[11px] leading-snug text-[var(--muted-foreground)]">
          {t(cap.description)}
        </div>
      </div>
      {selected && (
        <Check
          size={14}
          strokeWidth={2}
          className="shrink-0 text-[var(--primary)]"
        />
      )}
    </button>
  );
}

/**
 * The composer's primary action is a single control that spans the whole
 * turn — send, working, stop — rather than two buttons that swap places at
 * the moment of the click. These are its four states; only the skin changes.
 */
type SendState = "idle" | "ready" | "streaming";

/**
 * `idle` keeps a legible glyph on a hairline ring instead of fading the whole
 * button down: a translucent arrow on an equally translucent fill left the
 * arrow invisible in every theme. Readiness is carried by colour (neutral →
 * primary), not by opacity.
 *
 * Translucency goes through `color-mix` rather than Tailwind's `/NN` opacity
 * modifier. Tailwind 3 can only apply that modifier to colours it can split
 * into channels, so `bg-[var(--primary)]/90` — where the variable holds a hex
 * literal — compiles to nothing at all. `hover:ring-[5px]` and the lift carry
 * the hover state here; the fill deliberately doesn't shift, which also keeps
 * it from having to mix in a direction that reads right on all four themes.
 */
const SEND_STATE_CLASS: Record<SendState, string> = {
  idle: "cursor-default text-[var(--muted-foreground)] ring-1 ring-inset ring-[var(--border)]",
  ready:
    "bg-[var(--primary)] text-[var(--primary-foreground)] ring-[3px] ring-[color-mix(in_srgb,var(--primary)_18%,transparent)] hover:-translate-y-px hover:ring-[5px]",
  streaming: "bg-[var(--primary)] text-[var(--primary-foreground)]",
};

export default memo(function ChatComposer({
  composerRef,
  capMenuRef,
  capBtnRef,
  spaceMenuRef,
  spaceBtnRef,
  dragCounter,
  dragging,
  capMenuOpen,
  spaceMenuOpen,
  hasMessages,
  attachments,
  attachmentError,
  activeCap,
  llmOptions,
  activeLLMDefault,
  llmSelection,
  llmOptionsLoading,
  llmOptionsError,
  onRefreshLLMOptions,
  contextBudget = null,
  selectedHistorySessions,
  isStreaming,
  awaitingUserReply = false,
  capabilities,
  onSetCapMenuOpen,
  onSetSpaceMenuOpen,
  onSelectLLM,
  onSelectHistoryPicker,
  onSend,
  onRemoveAttachment,
  onPreviewAttachment,
  onRemoveHistory,
  onDragEnter,
  onDragLeave,
  onDragOver,
  onDrop,
  onPaste,
  onAddFiles,
  onSelectCapability,
  onCancelStreaming,
  prefillInputRef,
  inputPlaceholder,
  inputPlaceholderCompletion,
  showCapabilityChip = true,
}: {
  composerRef: RefObject<HTMLDivElement | null>;
  capMenuRef: RefObject<HTMLDivElement | null>;
  capBtnRef: RefObject<HTMLButtonElement | null>;
  spaceMenuRef: RefObject<HTMLDivElement | null>;
  spaceBtnRef: RefObject<HTMLButtonElement | null>;
  dragCounter: RefObject<number>;
  dragging: boolean;
  capMenuOpen: boolean;
  spaceMenuOpen: boolean;
  hasMessages: boolean;
  attachments: PendingAttachment[];
  attachmentError: string | null;
  activeCap: CapabilityDef;
  llmOptions: LLMOption[];
  activeLLMDefault: LLMSelection | null;
  llmSelection: LLMSelection | null;
  llmOptionsLoading: boolean;
  llmOptionsError: boolean;
  onRefreshLLMOptions?: () => void;
  /**
   * Context-window breakdown measured on the last turn that reported one.
   * Null until the first turn completes — the chip is skipped entirely
   * in that case.
   */
  contextBudget?: ContextBudget | null;
  selectedHistorySessions: SelectedHistorySession[];
  isStreaming: boolean;
  /** The live turn is paused on an ask_user card and needs an answer. */
  awaitingUserReply?: boolean;
  capabilities: CapabilityDef[];
  onSetCapMenuOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  onSetSpaceMenuOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  onSelectLLM: (selection: LLMSelection | null) => void;
  onSelectHistoryPicker: () => void;
  onSend: (content: string) => void;
  onRemoveAttachment: (index: number) => void;
  onPreviewAttachment?: (index: number) => void;
  onRemoveHistory: (sessionId: string) => void;
  onDragEnter: (event: React.DragEvent) => void;
  onDragLeave: (event: React.DragEvent) => void;
  onDragOver: (event: React.DragEvent) => void;
  onDrop: (event: React.DragEvent) => void;
  onPaste: (event: React.ClipboardEvent) => void;
  onAddFiles: (files: File[]) => void;
  onSelectCapability: (value: string) => void;
  onCancelStreaming: () => void;
  /**
   * Optional ref the composer writes its ``prefillInput`` function into
   * once mounted, so the message-list side (specifically
   * ``AskUserOptions`` chips) can drop a string into the textarea
   * without owning the composer's imperative handle directly.
   */
  prefillInputRef?: React.MutableRefObject<((text: string) => void) | null>;
  /** Override the composer placeholder. */
  inputPlaceholder?: string;
  /** A line Tab accepts while the composer is empty. See ComposerInput. */
  inputPlaceholderCompletion?: string;
  /**
   * Hide the capability chip. A surface that only ever runs one capability
   * — and names it in its own chrome — gains nothing from a picker that
   * cannot pick anything.
   */
  showCapabilityChip?: boolean;
}) {
  const { t } = useTranslation();
  const CapIcon = activeCap.icon;

  const [hasContent, setHasContent] = useState(false);
  const [moreCapsOpen, setMoreCapsOpen] = useState(false);
  const [lastCapMenuOpen, setLastCapMenuOpen] = useState(capMenuOpen);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const restoreFocusOnReturnRef = useRef(false);
  const inputHandleRef = useRef<ComposerInputHandle>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  if (lastCapMenuOpen !== capMenuOpen) {
    setLastCapMenuOpen(capMenuOpen);
    if (!capMenuOpen) setMoreCapsOpen(false);
  }

  useEffect(() => {
    if (!prefillInputRef) return;
    prefillInputRef.current = (text: string) => {
      inputHandleRef.current?.setValue(text);
    };
    return () => {
      if (prefillInputRef) prefillInputRef.current = null;
    };
  }, [prefillInputRef]);

  // Microphone → speech-to-text. Appends the transcript to whatever is already
  // in the composer so a dictated phrase can be combined with typed text.
  const handleTranscript = useCallback((text: string) => {
    const current = inputHandleRef.current?.getValue() || "";
    const next = current.trim() ? `${current.trimEnd()} ${text}` : text;
    inputHandleRef.current?.setValue(next);
  }, []);
  const recorder = useVoiceRecorder(handleTranscript);

  // Composer-row compaction: when the available width drops below ~620 px
  // (e.g. the Viewer panel is open or the user is on a narrow viewport),
  // the cap chip + Tools/Attach/Space labels collide. We measure the
  // composer itself and flip those labels to icon-only below the
  // threshold. Count-badges stay visible so users still see how many
  // things are selected.
  const [composerCompact, setComposerCompact] = useState(false);
  useEffect(() => {
    const el = composerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    setComposerCompact(el.getBoundingClientRect().width < 620);
    const observer = new ResizeObserver(() => {
      if (composerRef.current) {
        setComposerCompact(
          composerRef.current.getBoundingClientRect().width < 620,
        );
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [composerRef]);

  const handlePickFiles = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileInputChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const picked = Array.from(event.target.files ?? []);
      if (picked.length) onAddFiles(picked);
      // Reset so picking the same file twice still triggers `change`.
      event.target.value = "";
    },
    [onAddFiles],
  );

  const focusTextarea = useCallback(() => {
    requestAnimationFrame(() => textareaRef.current?.focus());
  }, []);

  useEffect(() => {
    const rememberFocus = () => {
      restoreFocusOnReturnRef.current =
        document.activeElement === textareaRef.current;
    };
    const restoreFocus = () => {
      if (
        restoreFocusOnReturnRef.current &&
        document.visibilityState === "visible"
      ) {
        focusTextarea();
      }
    };

    window.addEventListener("blur", rememberFocus);
    window.addEventListener("focus", restoreFocus);
    document.addEventListener("visibilitychange", restoreFocus);
    return () => {
      window.removeEventListener("blur", rememberFocus);
      window.removeEventListener("focus", restoreFocus);
      document.removeEventListener("visibilitychange", restoreFocus);
    };
  }, [focusTextarea]);

  useEffect(() => {
    if (!hasMessages) focusTextarea();
  }, [hasMessages, focusTextarea]);

  const handleSelectCapability = useCallback(
    (value: string) => {
      setMoreCapsOpen(false);
      onSelectCapability(value);
    },
    [onSelectCapability, setMoreCapsOpen],
  );

  // Functional-update form keeps `handleInputChange` identity stable across
  // every keystroke (no `hasContent` in deps), so the memoized ComposerInput
  // doesn't get re-rendered just because we observed a content-empty toggle.
  const handleInputChange = useCallback((val: string) => {
    const next = !!val.trim();
    setHasContent((prev) => (prev === next ? prev : next));
  }, []);

  const doSend = useCallback(
    (content: string) => {
      onSend(content);
      setHasContent(false);
      inputHandleRef.current?.clear();
      // Sending can move focus to the button or rerender the empty-state
      // composer into the conversation layout. Restore it after that update
      // so the user can keep typing, including after switching back to the tab.
      focusTextarea();
    },
    [focusTextarea, onSend],
  );

  const hasReferences =
    !!attachments.length ||
    !!selectedHistorySessions.length;

  // The capability-config confirmation gate (Quiz / Visualize / Research)
  // was removed with those capabilities: no remaining capability needs
  // explicit configuration, so send is never config-gated.
  const hasIntent = hasContent || hasReferences;
  // A turn paused on a question is technically still streaming, but the only
  // thing that can move it forward is the user's answer. Locking the composer
  // there made the interactive card the ONLY way to answer — and left the
  // learner with no way out at all if the card failed to render.
  const streamingBlocksSend = isStreaming && !awaitingUserReply;
  const canSend = hasIntent && !streamingBlocksSend;

  const sendState: SendState = streamingBlocksSend
    ? "streaming"
    : !hasIntent
      ? "idle"
      : "ready";

  const spaceSelectionCounts: SpaceSelectionCounts = {
    attachments: attachments.length,
    chatHistory: selectedHistorySessions.length,
  };
  // Badge on the "+" button = how many things are selected through the
  // "+" menu.
  const contextSelectionCount = Object.values(spaceSelectionCounts).reduce(
    (total, count) => total + count,
    0,
  );

  // Unified reference tree above the textarea: Space references render as
  // quiet monochrome rows, collapsed behind a count by default. File
  // attachments intentionally stay OUT of the tree — they keep their preview
  // cards below the textarea.
  const contextTreeItems: ContextTreeItem[] = [
    ...selectedHistorySessions.map(
      (session): ContextTreeItem => ({
        key: `hist-${session.sessionId}`,
        icon: MessageSquare,
        kind: t("Chat History"),
        label: session.title,
        onRemove: () => onRemoveHistory(session.sessionId),
      }),
    ),
  ];

  const handleManualSend = useCallback(() => {
    if (!canSend) return;
    const content = inputHandleRef.current?.getValue() || "";
    doSend(content);
  }, [canSend, doSend]);

  // One button, so one handler: mid-turn the same control cancels — except
  // while the turn is waiting on the user, where sending IS how it continues.
  const handleSendButtonClick = useCallback(() => {
    if (streamingBlocksSend) {
      onCancelStreaming();
      return;
    }
    handleManualSend();
  }, [handleManualSend, streamingBlocksSend, onCancelStreaming]);

  const sendLabel =
    sendState === "streaming"
      ? t("Stop generating")
      : awaitingUserReply
        ? t("Send answer")
        : t("Send");
  const sendTitle = sendLabel;

  return (
    <div
      ref={composerRef}
      className={`relative z-20 mx-auto w-full shrink-0 px-6 pb-5 ${hasMessages ? "pt-1 max-w-[960px]" : "max-w-[768px]"}`}
      style={{
        transition: "max-width 650ms cubic-bezier(0.16, 1, 0.3, 1)",
      }}
    >
      {hasMessages && (
        <div className="pointer-events-none absolute inset-x-0 top-0 h-6 bg-gradient-to-b from-transparent to-[var(--background)]/72" />
      )}

      <div className="relative">
        <div
          className={`relative rounded-[26px] border bg-[var(--card)] shadow-[0_1px_2px_rgba(0,0,0,0.025),0_10px_28px_-10px_rgba(0,0,0,0.08)] transition-colors ${
            dragging
              ? "border-[var(--primary)] bg-[var(--primary)]/[0.03]"
              : "border-[var(--border)]/55"
          }`}
          onDragEnter={onDragEnter}
          onDragLeave={onDragLeave}
          onDragOver={onDragOver}
          onDrop={onDrop}
          data-drag-counter={dragCounter.current}
        >
          {dragging && (
            <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-[26px] border-2 border-dashed border-[var(--primary)]/50 bg-[var(--primary)]/[0.04]">
              <div className="flex flex-col items-center gap-1 text-[var(--primary)]">
                <Paperclip size={22} strokeWidth={1.6} />
                <span className="text-[13px] font-medium">
                  {t("Drop files here")}
                </span>
                <span className="text-[11px] text-[var(--primary)]/70">
                  {t("Images, Office docs, code & text")}
                </span>
              </div>
            </div>
          )}

          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept={ATTACHMENT_ACCEPT}
            onChange={handleFileInputChange}
            className="hidden"
            aria-hidden="true"
            tabIndex={-1}
          />

          {contextTreeItems.length > 0 && (
            // The reference zone reads as its own layer: a faint muted band
            // with a hairline against the input area, following the card's
            // top radius.
            <div className="rounded-t-[26px] border-b border-[var(--border)]/30 bg-[var(--muted)]/30 px-4 pb-2 pt-2.5">
              {/* Narrower than the composer on purpose — long titles
                  truncate early so the tree reads as an annotation, not a
                  content row. */}
              <div className="max-w-[min(560px,85%)]">
                <ContextReferenceTree
                  items={contextTreeItems}
                  direction="up"
                  summaryNoun={t("references")}
                />
              </div>
            </div>
          )}
          <ComposerInput
            ref={inputHandleRef}
            textareaRef={textareaRef}
            isStreaming={isStreaming}
            canSendEmpty={hasReferences}
            onSend={doSend}
            onInputChange={handleInputChange}
            onPaste={onPaste}
            selectedCounts={spaceSelectionCounts}
            onSelectAttach={handlePickFiles}
            onSelectHistoryPicker={onSelectHistoryPicker}
            placeholder={inputPlaceholder}
            placeholderCompletion={inputPlaceholderCompletion}
            minHeight={hasMessages ? 28 : 64}
          />

          {!!attachments.length && (
            <div className="flex flex-wrap gap-2 px-4 pb-2">
              {attachments.map((a, i) => {
                const previewLabel = t("Preview");
                const removeLabel = t("Remove attachment");
                if (
                  (a.type === "image" || isSvgFilename(a.filename)) &&
                  a.previewUrl
                ) {
                  return (
                    <div
                      key={`${a.filename}-${i}`}
                      className="group relative"
                      title={a.filename || previewLabel}
                    >
                      <button
                        type="button"
                        onClick={() => onPreviewAttachment?.(i)}
                        aria-label={previewLabel}
                        className="relative block h-16 w-16 overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--card)] transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]/40"
                      >
                        {/* Native <img> is safe for SVG: scripts inside an
                            SVG don't execute under <img> context. Next.js
                            <Image> rejects SVG by default. */}
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={a.previewUrl}
                          alt={a.filename || t("Attachment preview")}
                          className={`h-full w-full ${isSvgFilename(a.filename) ? "object-contain p-1" : "object-cover"}`}
                        />
                      </button>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onRemoveAttachment(i);
                        }}
                        aria-label={removeLabel}
                        className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-[var(--foreground)] text-[var(--background)] opacity-0 shadow-sm transition-opacity group-hover:opacity-100"
                      >
                        <X size={10} />
                      </button>
                    </div>
                  );
                }
                const spec = docIconFor(a.filename);
                const Icon = spec.Icon;
                const sizeLabel = a.size ? formatBytes(a.size) : "";
                return (
                  <div
                    key={`${a.filename}-${i}`}
                    className="group relative"
                    title={a.filename}
                  >
                    <button
                      type="button"
                      onClick={() => onPreviewAttachment?.(i)}
                      aria-label={previewLabel}
                      className="flex h-16 w-[160px] items-center gap-2.5 rounded-lg border border-[var(--border)] bg-[var(--card)] px-2.5 text-left transition-colors hover:border-[var(--primary)]/40 hover:bg-[var(--muted)]/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)]/40"
                    >
                      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-[var(--muted)]/60">
                        <Icon
                          size={22}
                          strokeWidth={1.5}
                          className={spec.tint}
                        />
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-[12px] font-medium text-[var(--foreground)]">
                          {a.filename}
                        </div>
                        <div className="truncate text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">
                          {sizeLabel
                            ? `${spec.label} · ${sizeLabel}`
                            : spec.label}
                        </div>
                      </div>
                    </button>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onRemoveAttachment(i);
                      }}
                      aria-label={removeLabel}
                      className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-[var(--foreground)] text-[var(--background)] opacity-0 shadow-sm transition-opacity group-hover:opacity-100"
                    >
                      <X size={10} />
                    </button>
                  </div>
                );
              })}
            </div>
          )}

          {attachmentError && (
            <div className="px-4 pb-2 text-[11px] text-red-600">
              {attachmentError}
            </div>
          )}

          {/* Claude-style chrome-free toolbar: no divider against the input
              area, no pill borders — quiet text/icon buttons that surface
              on hover. */}
          <div className="px-3 pb-2 pt-0.5">
            <div className="flex items-center gap-1">
              {showCapabilityChip && (
                <div className="relative">
                  <button
                    ref={capBtnRef}
                    onClick={() => onSetCapMenuOpen((v) => !v)}
                    className={`inline-flex h-8 shrink-0 items-center gap-1.5 rounded-lg px-2 text-[14px] font-medium transition-[background-color,color,transform] duration-150 active:scale-[0.97] ${
                      capMenuOpen
                        ? "bg-[var(--primary)]/10 text-[var(--primary)]"
                        : "text-[var(--foreground)] hover:bg-[var(--muted)]/55"
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-1.5">
                      <CapIcon
                        size={16}
                        strokeWidth={1.7}
                        className="shrink-0"
                      />
                      {composerCompact ? null : (
                        <span className="truncate">{t(activeCap.label)}</span>
                      )}
                    </span>
                    <ChevronDown
                      size={13}
                      strokeWidth={2}
                      className={`-mr-0.5 shrink-0 transition-transform duration-200 ${capMenuOpen ? "rotate-180" : ""}`}
                    />
                  </button>

                  {capMenuOpen && (
                    <div
                      ref={capMenuRef}
                      className="dt-popup-up absolute bottom-full left-0 z-50 mb-1.5 w-[260px] overflow-visible rounded-xl border border-[var(--border)] bg-[var(--popover)] py-1 shadow-lg backdrop-blur-md"
                    >
                      {capabilities
                        .filter((cap) => !cap.secondary)
                        .map((cap) => (
                          <CapMenuItem
                            key={cap.value}
                            cap={cap}
                            selected={activeCap.value === cap.value}
                            onSelect={handleSelectCapability}
                          />
                        ))}
                      {(() => {
                        const loopCaps = capabilities.filter(
                          (cap) => cap.secondary,
                        );
                        if (loopCaps.length === 0) return null;
                        const loopSelected = loopCaps.some(
                          (cap) => cap.value === activeCap.value,
                        );
                        return (
                          <div
                            className="group/more relative"
                            onMouseEnter={() => setMoreCapsOpen(true)}
                            onMouseLeave={() => setMoreCapsOpen(false)}
                            onFocus={() => setMoreCapsOpen(true)}
                            onBlur={(event) => {
                              const next = event.relatedTarget;
                              if (
                                !next ||
                                !event.currentTarget.contains(next as Node)
                              ) {
                                setMoreCapsOpen(false);
                              }
                            }}
                          >
                            <button
                              type="button"
                              aria-haspopup="menu"
                              aria-expanded={moreCapsOpen}
                              onClick={() => setMoreCapsOpen((open) => !open)}
                              className={`flex w-full items-center gap-2.5 px-3 py-1.5 text-left transition-colors ${
                                moreCapsOpen
                                  ? "bg-[var(--muted)]/45"
                                  : "group-hover/more:bg-[var(--muted)]/45"
                              } ${
                                loopSelected && !moreCapsOpen
                                  ? "bg-[var(--primary)]/[0.06]"
                                  : ""
                              }`}
                            >
                              <Sparkles
                                size={15}
                                strokeWidth={1.7}
                                className={`shrink-0 ${loopSelected ? "text-[var(--primary)]" : "text-[var(--muted-foreground)]"}`}
                              />
                              <div className="min-w-0 flex-1">
                                <div className="truncate text-[12.5px] font-medium leading-snug text-[var(--foreground)]">
                                  {t("More Capabilities")}
                                </div>
                                <div className="truncate text-[11px] leading-snug text-[var(--muted-foreground)]">
                                  {t("Agent-loop driven modes")}
                                </div>
                              </div>
                              <ChevronRight
                                size={14}
                                strokeWidth={2}
                                className="shrink-0 text-[var(--muted-foreground)]"
                              />
                            </button>
                            {/* Right flyout. ``pl-1.5`` is a pointer bridge so the
                              cursor can cross the gap without dropping hover;
                              click/focus also open it for touch and keyboard. */}
                            <div
                              className={`absolute bottom-0 left-full z-50 pl-1.5 transition-opacity duration-150 ${
                                moreCapsOpen
                                  ? "visible opacity-100"
                                  : "invisible opacity-0"
                              }`}
                            >
                              <div className="w-[240px] overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--popover)] py-1 shadow-lg backdrop-blur-md">
                                {loopCaps.map((cap) => (
                                  <CapMenuItem
                                    key={cap.value}
                                    cap={cap}
                                    selected={activeCap.value === cap.value}
                                    onSelect={handleSelectCapability}
                                  />
                                ))}
                              </div>
                            </div>
                          </div>
                        );
                      })()}
                    </div>
                  )}
                </div>
              )}

              <div className="relative flex min-w-0 flex-1 items-center">
                <button
                  ref={spaceBtnRef}
                  type="button"
                  onClick={() => onSetSpaceMenuOpen((v) => !v)}
                  title={t("Add files & context")}
                  aria-label={t("Add files & context")}
                  className={`relative flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-[background-color,color,transform] duration-150 active:scale-90 ${
                    spaceMenuOpen
                      ? "bg-[var(--muted)] text-[var(--foreground)]"
                      : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
                  }`}
                >
                  <Plus size={20} strokeWidth={1.8} />
                  {contextSelectionCount > 0 && (
                    <span className="absolute -right-0.5 -top-0.5 flex h-[13px] min-w-[13px] items-center justify-center rounded-full bg-[var(--primary)] px-[3px] text-[8px] font-semibold leading-none text-[var(--primary-foreground)] ring-[1.5px] ring-[var(--card)]">
                      {contextSelectionCount}
                    </span>
                  )}
                </button>
                <AnimatePresence>
                  {spaceMenuOpen && (
                    <motion.div
                      ref={spaceMenuRef}
                      className="absolute bottom-full left-0 z-50 mb-1.5"
                      style={{ transformOrigin: "bottom left" }}
                      initial={{ opacity: 0, y: 6, scale: 0.96 }}
                      animate={{ opacity: 1, y: 0, scale: 1 }}
                      exit={{ opacity: 0, y: 4, scale: 0.97 }}
                      transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
                    >
                      <ChatSpaceMenu
                        variant="toolbar"
                        selectedCounts={spaceSelectionCounts}
                        onSelectItem={(key) => {
                          onSetSpaceMenuOpen(false);
                          if (key === "attach") handlePickFiles();
                          else if (key === "chat_history") onSelectHistoryPicker();
                        }}
                      />
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>

              <div className="ml-auto flex shrink-0 items-center gap-1.5">
                <ModelSelector
                  options={llmOptions}
                  activeDefault={activeLLMDefault}
                  value={llmSelection}
                  loading={llmOptionsLoading}
                  error={llmOptionsError}
                  onChange={onSelectLLM}
                  onRefresh={onRefreshLLMOptions}
                />
                {contextBudget ? (
                  <ContextBudgetChip budget={contextBudget} />
                ) : null}

                <button
                  type="button"
                  onClick={recorder.toggle}
                  disabled={recorder.state === "transcribing" || isStreaming}
                  className={`group relative inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] transition-[background-color,color,transform] duration-150 active:scale-90 disabled:opacity-40 ${
                    recorder.state === "recording"
                      ? "bg-red-500/15 text-red-500"
                      : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
                  }`}
                  aria-label={
                    recorder.state === "recording"
                      ? t("Stop recording")
                      : t("Record voice")
                  }
                  title={
                    recorder.error ||
                    (recorder.state === "recording"
                      ? t("Stop recording")
                      : t("Record voice"))
                  }
                >
                  {recorder.state === "recording" && (
                    <span className="pointer-events-none absolute inset-0 rounded-[10px] border border-red-500/40 animate-pulse" />
                  )}
                  {recorder.state === "transcribing" ? (
                    <Loader2
                      size={16}
                      strokeWidth={1.9}
                      className="animate-spin"
                    />
                  ) : (
                    <Mic size={16} strokeWidth={1.9} />
                  )}
                </button>

                {/* The thing you press is the thing that's working is the
                    thing you press to stop — one element for the whole turn,
                    so the button never swaps out from under the cursor at the
                    moment of the click. The glyph crossfades arrow→square in
                    place (both stacked in the same grid cell) and the progress
                    ring moves to the perimeter, where it can spin without
                    fighting the square for the same space. */}
                <button
                  type="button"
                  onClick={handleSendButtonClick}
                  disabled={sendState === "idle"}
                  className={`group relative ml-1 inline-grid h-8 w-8 shrink-0 place-items-center rounded-full transition-[background-color,box-shadow,transform] duration-200 active:scale-95 ${SEND_STATE_CLASS[sendState]}`}
                  aria-label={sendLabel}
                  title={sendTitle}
                >
                  {sendState === "streaming" && (
                    // Outside the fill, so "still working" reads at a glance
                    // and dims on hover to hand the control back as "stop".
                    <span className="pointer-events-none absolute -inset-[3px] rounded-full border-2 border-[color-mix(in_srgb,var(--primary)_15%,transparent)] border-t-[var(--primary)] animate-spin transition-opacity group-hover:opacity-30" />
                  )}
                  <ArrowUp
                    size={16}
                    strokeWidth={2.5}
                    className={`col-start-1 row-start-1 transition-[opacity,transform] duration-200 ${
                      sendState === "streaming"
                        ? "scale-50 opacity-0"
                        : "scale-100 opacity-100"
                    }`}
                  />
                  <Square
                    size={10}
                    strokeWidth={2.6}
                    className={`col-start-1 row-start-1 fill-current transition-[opacity,transform] duration-200 ${
                      sendState === "streaming"
                        ? "scale-100 opacity-100"
                        : "scale-50 opacity-0"
                    }`}
                  />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
});
