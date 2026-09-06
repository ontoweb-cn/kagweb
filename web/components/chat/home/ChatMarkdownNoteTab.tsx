"use client";

import { useCallback, useEffect, useState, type KeyboardEvent } from "react";
import { NotebookPen } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useAuthStatus } from "@/hooks/useAuthStatus";
import {
  loadChatMarkdownNoteDraft,
  EMPTY_CHAT_MARKDOWN_NOTE_DRAFT,
  saveChatMarkdownNoteDraft,
} from "@/lib/chat-markdown-note";

/**
 * Session-scoped Markdown scratchpad shown as a Viewer tab. Drafts persist
 * to localStorage per user + session; there is no backend store behind it.
 */
export default function ChatMarkdownNoteTab({
  sessionId,
}: {
  sessionId: string | null;
}) {
  const { t } = useTranslation();
  const auth = useAuthStatus();
  const ownerId =
    !auth.loading && auth.statusAvailable
      ? auth.enabled
        ? auth.userId
        : "local"
      : null;
  const draftScope = ownerId ? `${ownerId}:${sessionId ?? "pending"}` : null;
  const [loadedScope, setLoadedScope] = useState<string | null>(null);
  const [draft, setDraft] = useState(() => ({
    ...EMPTY_CHAT_MARKDOWN_NOTE_DRAFT,
  }));
  const { title, content } = draft;

  useEffect(() => {
    if (!ownerId || !draftScope) return;
    setDraft(loadChatMarkdownNoteDraft(ownerId, sessionId));
    setLoadedScope(draftScope);
  }, [draftScope, ownerId, sessionId]);

  useEffect(() => {
    if (!ownerId || loadedScope !== draftScope) return;
    saveChatMarkdownNoteDraft(ownerId, sessionId, draft);
  }, [draft, draftScope, loadedScope, ownerId, sessionId]);

  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLTextAreaElement>) => {
    // No save action: the draft persists on every keystroke.
    void event;
  }, []);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-[var(--border)]/40 bg-[var(--card)] px-3 py-2">
        <NotebookPen
          size={15}
          strokeWidth={1.8}
          className="shrink-0 text-[var(--muted-foreground)]"
        />
        <input
          type="text"
          value={title}
          onChange={(event) => {
            setDraft((latest) => ({ ...latest, title: event.target.value }));
          }}
          placeholder={t("Untitled note")}
          aria-label={t("Note title")}
          maxLength={200}
          className="min-w-[140px] flex-1 basis-40 rounded-md border border-transparent bg-transparent px-1 py-1 text-[13px] font-medium text-[var(--foreground)] outline-none transition-colors placeholder:text-[var(--muted-foreground)]/60 hover:border-[var(--border)]/50 focus:border-[var(--primary)]/40 focus:bg-[var(--background)]"
        />
      </div>
      <textarea
        value={content}
        onChange={(event) => {
          setDraft((latest) => ({ ...latest, content: event.target.value }));
        }}
        onKeyDown={handleKeyDown}
        placeholder={t("Start writing in Markdown...")}
        aria-label={t("Markdown")}
        className="min-h-0 flex-1 resize-none bg-[var(--card)] px-3 py-3 font-mono text-[12.5px] leading-relaxed text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60"
      />
    </div>
  );
}
