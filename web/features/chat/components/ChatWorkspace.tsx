'use client'

import dynamic from 'next/dynamic'
import { type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useAuthStatus } from '@/hooks/useAuthStatus'
import { useChatRouteSession } from '@/features/chat/controllers/useChatRouteSession'

import { NotebookPen, PenLine, type LucideIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import type { SelectedHistorySession } from '@/components/chat/HistorySessionPicker'
import ChatComposer from '@/components/chat/home/ChatComposer'
import type { ContextBudget } from '@/components/chat/home/ContextBudgetChip'
import { ChatMessageList } from '@/features/chat/messages'
import { TurnNavigator } from '@/components/chat/home/TurnNavigator'
import SessionLoadingView from '@/components/chat/home/SessionLoadingView'
import { SESSION_LOAD_TIMEOUT_MS, shouldSurfaceLoadFailure } from '@/lib/session-load'
// Imported eagerly so the drawer shell is always mounted off-screen —
// clicking a chip becomes a single CSS class flip, no chunk fetch + double
// render. The heavy renderers inside still load lazily.
import FilePreviewDrawer from '@/components/chat/preview/FilePreviewDrawer'
import SessionDagPanel from '@/features/chat/dag/SessionDagPanel'
import { buildSessionActivity } from '@/components/chat/home/SessionActivityPanel'
import Tooltip from '@/components/common/Tooltip'
import SessionViewerPanel, {
  type SessionViewerPanelHandle,
} from '@/components/chat/home/SessionViewerPanel'
import type { ToolOutputTraceTarget } from '@/lib/session-activity'
import {
  BookmarkPlus,
  Download,
  GraduationCap,
  Network,
  PanelRight,
  Terminal,
} from 'lucide-react'
import {
  useChatStateAdapter,
  type MessageAttachment,
} from '@/features/chat/ChatStateAdapter'
import { useAppShell } from '@/context/AppShellContext'

import type { FilePreviewSource } from '@/components/chat/preview/previewerFor'
import type { StreamEvent } from '@/features/chat/model/protocol'
import {
  fileToPendingAttachment,
  selectAttachmentFiles,
  type PendingAttachment,
} from '@/features/chat/controllers/pending-attachments'
import { readChatLaunchIntent } from '@/lib/chat-launch-intent'
import { useAttachmentLimits } from '@/lib/attachment-limits'
import { hasPendingAskUser } from '@/lib/ask-user-state'
import { useTraceMode } from '@/hooks/useTraceMode'
import { useChatAutoScroll } from '@/hooks/useChatAutoScroll'
import { useMeasuredHeight } from '@/hooks/useMeasuredHeight'
import { useSetupSync } from '@/hooks/useSetupSync'
import { consumePendingPrompt } from '@/lib/pending-prompt'
import { useLLMOptions } from '@/hooks/useLLMOptions'
import { getChatCapability } from '@/features/capabilities/presentation'
import { useCapabilityCatalog } from '@/features/capabilities/useCapabilityCatalog'
import { browserStorage } from '@/shared/storage'
import { downloadChatMarkdown } from '@/lib/chat-export'
import { buildChatOutline, scrollToChatTurn } from '@/lib/chat-outline'
import { isPlaceholderSessionTitle } from '@/lib/session-title'
import {
  normalizeSelectedText,
  textFromDomSelection,
} from '@/lib/selection-tutor'

const HistorySessionPicker = dynamic(() => import('@/components/chat/HistorySessionPicker'), {
  ssr: false,
})

/* ------------------------------------------------------------------ */
/*  Helpers                                                           */
/* ------------------------------------------------------------------ */

/**
 * Read the context-window measurement a finished turn attached to its
 * `result` event. Scanned newest-first because one turn can emit several
 * results (a consulted subagent emits its own) and only the chat loop's
 * closing one carries the budget; older backends emit none at all, and the
 * measurement is allowed to degrade to "absent" rather than fail a turn.
 */
function readContextBudget(events: StreamEvent[] | undefined): ContextBudget | null {
  if (!events) return null
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const ev = events[i]
    if (ev.type !== 'result') continue
    const meta = ev.metadata?.metadata as Record<string, unknown> | undefined
    const budget = meta?.context_budget as ContextBudget | undefined
    if (
      budget &&
      typeof budget.window === 'number' &&
      typeof budget.used_tokens === 'number' &&
      Array.isArray(budget.segments)
    ) {
      return budget
    }
  }
  return null
}

function normalizedTraceText(value: unknown): string {
  return String(value || '')
    .toLowerCase()
    .replace(/\s+/g, ' ')
    .trim()
}

/** Score a rendered answer block by how many retrieval anchors it mentions. */
function answerTraceScore(element: Element, terms: string[]): number {
  const text = normalizedTraceText(element.textContent)
  if (!text) return 0
  let score = 0
  for (const term of terms) {
    const normalizedTerm = normalizedTraceText(term)
    if (normalizedTerm.length >= 2 && text.includes(normalizedTerm)) {
      score += Math.min(normalizedTerm.length, 48)
    }
  }
  return score
}

/**
 * Resolve a retrieval provenance click to the assistant answer that consumed
 * the tool result. Trace rows describe *how* an answer was produced; the user
 * asked to inspect *what* was produced, so the answer body is preferred and
 * the trace row remains only as a legacy fallback.
 */
type AnswerTraceTarget = {
  anchor: HTMLElement
  targets: HTMLElement[]
}

/** Simple renderer maps markdown headings to semantically-bold paragraphs. */
function isRenderedHeading(element: Element): boolean {
  return /^H[1-6]$/.test(element.tagName) || Boolean(element.matches('p.font-semibold'))
}

function isRenderableTraceBlock(element: Element): boolean {
  const text = normalizedTraceText(element.textContent)
  return text.length > 0 || Boolean(element.querySelector('img, svg, canvas, video, iframe'))
}

function findAnswerTraceTarget(
  container: HTMLElement | null | undefined,
  detail: ToolOutputTraceTarget
): AnswerTraceTarget | null {
  const message =
    detail.messageIndex !== undefined
      ? container?.querySelector<HTMLElement>(`[data-chat-message-index="${detail.messageIndex}"]`)
      : null
  const outputRoots = message
    ? Array.from(message.querySelectorAll<HTMLElement>('[data-chat-assistant-output="true"]'))
    : []

  const selection: {
    current: { heading: HTMLElement | null; content: HTMLElement[] } | null
  } = { current: null }
  let bestScore = 0
  let headingOnlyBest: { heading: HTMLElement; content: HTMLElement[] } | null = null
  let headingOnlyBestScore = 0
  for (const root of outputRoots) {
    // Markdown content is rendered inside one or more `.md-renderer` roots.
    // Scoring their direct children avoids choosing a nested list row while
    // its enclosing list is the actual answer section being highlighted.
    const markdownRoots = Array.from(root.querySelectorAll<HTMLElement>('.md-renderer'))
    for (const markdownRoot of markdownRoots.length > 0 ? markdownRoots : [root]) {
      const blocks = Array.from(markdownRoot.children).filter(
        (element): element is HTMLElement => element instanceof HTMLElement
      )
      if (blocks.length === 0) continue

      let currentHeading: HTMLElement | null = null
      let currentContent: HTMLElement[] = []
      const evaluateSection = () => {
        const content = currentContent.filter(isRenderableTraceBlock)
        if (content.length === 0) return

        // Content before the first heading has no semantic section boundary.
        // Select the strongest individual block instead of flooding the
        // screen with every top-level paragraph/list in the answer.
        if (!currentHeading) {
          for (const element of content) {
            const score = answerTraceScore(element, detail.terms ?? [])
            if (score > bestScore) {
              selection.current = { heading: null, content: [element] }
              bestScore = score
            }
          }
          return
        }

        const contentScore = content.reduce(
          (score, element) => score + answerTraceScore(element, detail.terms ?? []),
          0
        )
        const headingScore = currentHeading
          ? answerTraceScore(currentHeading, detail.terms ?? [])
          : 0

        if (contentScore > 0 && contentScore > bestScore) {
          selection.current = { heading: currentHeading, content }
          bestScore = contentScore
        }

        // Retrieval terms are often the entity named in the heading, while
        // the generated body explains it with pronouns or synonyms. Keep the
        // closest heading-backed section as a body-first fallback.
        if (currentHeading && headingScore > headingOnlyBestScore) {
          headingOnlyBest = { heading: currentHeading, content }
          headingOnlyBestScore = headingScore
        }
      }

      for (const block of blocks) {
        if (isRenderedHeading(block)) {
          evaluateSection()
          currentHeading = block
          currentContent = []
        } else {
          currentContent.push(block)
        }
      }
      evaluateSection()
    }
  }

  if (!selection.current && headingOnlyBest) selection.current = headingOnlyBest
  const best = selection.current
  if (best) {
    const { heading, content } = best
    return {
      anchor: heading ?? content[0],
      targets: heading ? [heading, ...content] : content,
    }
  }

  if (outputRoots[0]) return { anchor: outputRoots[0], targets: [outputRoots[0]] }

  // Persisted sessions from older builds may not have an answer-body marker.
  // Prefer the enclosing assistant message before falling back to the trace.
  if (message) return { anchor: message, targets: [message] }
  if (detail.callId) {
    const target =
      container?.querySelector<HTMLElement>(`[data-trace-call-id="${detail.callId}"]`) ?? null
    return target ? { anchor: target, targets: [target] } : null
  }
  return null
}

/* ------------------------------------------------------------------ */
/*  Chat page                                                         */
/* ------------------------------------------------------------------ */

export default function ChatWorkspace() {
  const { router, sessionId: sessionIdParam } = useChatRouteSession()
  const { t } = useTranslation()
  const [traceMode, setTraceMode] = useTraceMode()
  const {
    capabilities,
    visibleCapabilities,
    isLoading: isCapabilityCatalogLoading,
  } = useCapabilityCatalog()
  const { setActiveSessionId, language: appLanguage } = useAppShell()

  const {
    state,
    setCapability,
    setLLMSelection,
    sendMessage,
    cancelStreamingTurn,
    submitUserReply,
    regenerateLastMessage,
    deleteTurn,
    editMessage,
    switchBranch,
    newSession,
    loadSession,
    showCachedSession,
    renameSessionTitle,
  } = useChatStateAdapter()

  const {
    options: llmOptions,
    activeDefault: activeLLMDefault,
    loading: llmOptionsLoading,
    error: llmOptionsError,
    refresh: refreshLLMOptions,
  } = useLLMOptions()
  // The per-turn model picker only makes sense when the configured backend
  // actually consumes a model (one-shot CLI family) — the backend decides,
  // via /api/auth/status's model_selector_enabled.
  const auth = useAuthStatus()
  // The tool set is the active capability's allow-list. There is no user-level
  // tool toggle any more: the chat capability delegates each turn to an
  // external agent backend, which owns its own tools, so a KAGWeb-side switch
  // could never take effect. The composer never exposed a picker either.
  const [attachments, setAttachments] = useState<PendingAttachment[]>([])
  const attachmentLimits = useAttachmentLimits()
  const [dragging, setDragging] = useState(false)
  const [attachmentError, setAttachmentError] = useState<string | null>(null)
  const [previewSource, setPreviewSource] = useState<FilePreviewSource | null>(null)
  // Right-side panels — Activity (floating cards) and Viewer (full sidebar
  // with tabs for file previews + web pages). Each independently togglable
  // and persisted across reloads.
  //
  // We initialise both to `false` so the SSR-rendered HTML matches the
  // first client render exactly (no hydration mismatch). The persisted
  // preference is then applied in a post-mount effect below.
  // Single right-side panel: the Activity/Viewer. Its home view is the
  // session activity; files and web pages open as tabs alongside it.
  const [viewerPanelOpen, setViewerPanelOpen] = useState(false);
  // Selects the persistent DAG home inside the shared Activity/Viewer panel.
  const [sessionDagOpen, setSessionDagOpen] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined') return
    if (browserStorage.readRaw('local', 'dt:chat:viewer-panel') === '1') {
      setViewerPanelOpen(true)
    }
  }, [])
  const setViewerOpen = useCallback((next: boolean) => {
    setViewerPanelOpen(next)
    if (typeof window !== 'undefined') {
      browserStorage.writeRaw('local', 'dt:chat:viewer-panel', next ? '1' : '0')
    }
  }, [])
  const toggleViewerPanel = useCallback(() => {
    setViewerPanelOpen(prev => {
      const next = !prev
      if (typeof window !== 'undefined') {
        browserStorage.writeRaw('local', 'dt:chat:viewer-panel', next ? '1' : '0')
      }
      return next
    })
  }, [])
  /**
   * Force the panel open on its Activity home. Used by the send-gate when the
   * user tries to send while the active capability still needs its config
   * confirmed — the config card lives on the Activity home, so we open the
   * panel and switch to it. Also used by the capability-switch auto-open
   * effect below.
   */
  const viewerPanelRef = useRef<SessionViewerPanelHandle | null>(null)
  const traceFlashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const traceFlashTargetsRef = useRef<HTMLElement[]>([])
  const ensureActivityPanelOpen = useCallback(() => {
    setViewerOpen(true)
    viewerPanelRef.current?.focusActivityHome()
  }, [setViewerOpen])
  const attachmentErrorTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [capMenuOpen, setCapMenuOpen] = useState(false)
  const [showHistoryPicker, setShowHistoryPicker] = useState(false)
  const [selectedHistorySessions, setSelectedHistorySessions] = useState<SelectedHistorySession[]>(
    []
  )
  const [spaceMenuOpen, setSpaceMenuOpen] = useState(false)
  const dragCounter = useRef(0)
  const capMenuRef = useRef<HTMLDivElement>(null)
  const capBtnRef = useRef<HTMLButtonElement>(null)
  const spaceMenuRef = useRef<HTMLDivElement>(null)
  const spaceBtnRef = useRef<HTMLButtonElement>(null)
  const initialLoadRef = useRef(false)
  // Session-loading overlay: shown while navigating from chat-history →
  // session detail. Holds an AbortController so the user can cancel.
  const [sessionLoading, setSessionLoading] = useState(false)
  // A load that ended without a session: terminal, and retryable. Kept
  // separate from `sessionLoading` so the overlay can tell "still
  // arriving" apart from "never arrived".
  const [sessionLoadFailed, setSessionLoadFailed] = useState(false)
  const loadAbortRef = useRef<AbortController | null>(null)
  // Bridge ref: ``ChatComposer`` writes a prefill function into this on
  // mount; ``ChatMessageList`` reads it via ``handlePrefillComposer`` so an
  // ``AskUserOptions`` chip click can drop text into the composer textarea.
  const prefillInputRef = useRef<((text: string) => void) | null>(null)
  const handlePrefillComposer = useCallback((text: string) => {
    prefillInputRef.current?.(text)
  }, [])

  // A message handed over by another page (Settings' "set up with KAGWeb"
  // button). Prefilled rather than sent: the user reads what will be asked and
  // presses enter themselves. Consumed once, so a refresh does not retype it.
  //
  // Retried on a short bounded schedule rather than fired once: the composer
  // installs its prefill bridge from its own effect, and it is not mounted at
  // all while a session is still loading. A single attempt would land on a null
  // ref and drop the message silently — the user arrives from Settings at an
  // empty box with no idea the button did anything.
  useEffect(() => {
    // Two producers: the Settings hub writes the unscoped slot, and a Course
    // Study hand-off to chat writes the "chat" one.
    const pending = consumePendingPrompt() || consumePendingPrompt('chat')
    if (!pending) return
    let attempts = 0
    let timer: ReturnType<typeof setTimeout>
    const attempt = () => {
      if (prefillInputRef.current) {
        handlePrefillComposer(pending)
        return
      }
      if (attempts++ >= 20) return // ~2s, then give up quietly
      timer = setTimeout(attempt, 100)
    }
    timer = setTimeout(attempt, 0)
    return () => clearTimeout(timer)
  }, [handlePrefillComposer])

  const activeCap = useMemo(
    () =>
      capabilities.find(capability => capability.value === (state.activeCapability || '')) ??
      getChatCapability(state.activeCapability),
    [capabilities, state.activeCapability]
  )

  // Trace-flash timer cleanup.
  useEffect(() => {
    return () => {
      if (traceFlashTimerRef.current) clearTimeout(traceFlashTimerRef.current)
    }
  }, [])

  // Adopt UI preferences the assistant changed mid-conversation: the browser
  // otherwise keeps serving its own cached language/theme and the user is told
  // "done" while nothing visibly changes.
  useSetupSync(state.messages)
  const hasMessages = state.messages.length > 0
  // Time-of-day greeting: seeded once on mount from the user's local clock so
  // the heading stays stable while they're on the page. State (not useMemo)
  // because the random pick would otherwise mismatch SSR ↔ client hydration.
  const [welcomeGreeting, setWelcomeGreeting] = useState<string>('What would you like to learn?')
  useEffect(() => {
    const hour = new Date().getHours()
    let bucket: string[]
    if (hour >= 5 && hour < 12) {
      bucket = [
        'Good morning.',
        "Morning — let's learn something.",
        'What would you like to learn?',
      ]
    } else if (hour >= 12 && hour < 17) {
      bucket = [
        'Good afternoon.',
        "Afternoon — what's on your mind?",
        'What would you like to learn?',
      ]
    } else if (hour >= 17 && hour < 22) {
      bucket = [
        'Good evening.',
        'Evening — what shall we explore?',
        'What would you like to learn?',
      ]
    } else {
      bucket = ["It's late today.", 'Burning the midnight oil?', 'What would you like to learn?']
    }
    setWelcomeGreeting(bucket[Math.floor(Math.random() * bucket.length)])
  }, [])
  const firstUserTitle = useMemo(
    () =>
      state.messages
        .find(msg => msg.role === 'user')
        ?.content.trim()
        .replace(/\s+/g, ' ')
        .slice(0, 80) || '',
    [state.messages]
  )
  const persistedSessionTitle = state.sessionTitle.trim()
  const displaySessionTitle = isPlaceholderSessionTitle(persistedSessionTitle)
    ? firstUserTitle || t('New chat')
    : persistedSessionTitle
  const canRenameSession = Boolean(state.sessionId)
  const titleInputRef = useRef<HTMLInputElement | null>(null)
  const skipTitleCommitRef = useRef(false)
  const [sessionTitleDraft, setSessionTitleDraft] = useState(displaySessionTitle)
  const [sessionTitleEditing, setSessionTitleEditing] = useState(false)
  const [sessionTitleSaving, setSessionTitleSaving] = useState(false)
  const [sessionTitleError, setSessionTitleError] = useState<string | null>(null)
  useEffect(() => {
    if (sessionTitleEditing) return
    setSessionTitleDraft(displaySessionTitle)
  }, [displaySessionTitle, sessionTitleEditing])
  useEffect(() => {
    if (!sessionTitleEditing) return
    window.requestAnimationFrame(() => {
      titleInputRef.current?.focus()
      titleInputRef.current?.select()
    })
  }, [sessionTitleEditing])
  const startSessionTitleEdit = useCallback(() => {
    if (!canRenameSession) return
    skipTitleCommitRef.current = false
    setSessionTitleError(null)
    setSessionTitleDraft(displaySessionTitle)
    setSessionTitleEditing(true)
  }, [canRenameSession, displaySessionTitle])
  const cancelSessionTitleEdit = useCallback(() => {
    skipTitleCommitRef.current = true
    setSessionTitleDraft(displaySessionTitle)
    setSessionTitleError(null)
    setSessionTitleEditing(false)
  }, [displaySessionTitle])
  const commitSessionTitleEdit = useCallback(async () => {
    if (skipTitleCommitRef.current) {
      skipTitleCommitRef.current = false
      return
    }
    const next = sessionTitleDraft.trim()
    if (!next) {
      setSessionTitleDraft(displaySessionTitle)
      setSessionTitleEditing(false)
      return
    }
    if (!canRenameSession || next === persistedSessionTitle) {
      setSessionTitleDraft(next || displaySessionTitle)
      setSessionTitleEditing(false)
      return
    }
    setSessionTitleSaving(true)
    setSessionTitleError(null)
    try {
      await renameSessionTitle(next)
      setSessionTitleEditing(false)
    } catch (error) {
      console.error('Failed to rename session:', error)
      setSessionTitleError(t('Rename failed'))
      titleInputRef.current?.focus()
    } finally {
      setSessionTitleSaving(false)
    }
  }, [
    canRenameSession,
    displaySessionTitle,
    persistedSessionTitle,
    renameSessionTitle,
    sessionTitleDraft,
    t,
  ])
  const handleSessionTitleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === 'Enter') {
        event.preventDefault()
        void commitSessionTitleEdit()
      } else if (event.key === 'Escape') {
        event.preventDefault()
        cancelSessionTitleEdit()
      }
    },
    [cancelSessionTitleEdit, commitSessionTitleEdit]
  )
  const { ref: composerRef, height: composerHeight } = useMeasuredHeight<HTMLDivElement>()
  // Chat-history references are session ids sent as one backend field.
  const historyReferencesPayload = useMemo(
    () => Array.from(new Set(selectedHistorySessions.map(session => session.sessionId))),
    [selectedHistorySessions]
  )
  const lastMessage = state.messages[state.messages.length - 1]
  const {
    containerRef: messagesContainerRef,
    endRef: messagesEndRef,
    shouldAutoScrollRef,
    scrollToBottom,
    handleScroll: handleMessagesScroll,
  } = useChatAutoScroll({
    hasMessages,
    isStreaming: state.isStreaming,
    composerHeight,
    messageCount: state.messages.length,
    lastMessageContent: lastMessage?.content,
    lastEventCount: lastMessage?.events?.length,
  })

  // ─── Turn navigator ───
  // One tick per question the user asked, rendered in the transcript's
  // left gutter (see ``TurnNavigator``). The outline is derived from the
  // same visible-path walk the message list uses, so switching an edit
  // branch reshapes both together.
  const chatOutline = useMemo(
    () => buildChatOutline(state.messages, state.selectedBranches),
    [state.messages, state.selectedBranches]
  )
  /** Bring a question back on screen and mark where the user landed. */
  const jumpToTurn = useCallback(
    (key: string) => {
      const container = messagesContainerRef.current
      // 56 px clears the scrollport's top fade so the bubble lands fully
      // opaque rather than half-dissolved under the mask.
      if (scrollToChatTurn(container, key, { topOffset: 56, flash: true })) {
        // Release the streaming pin: otherwise the next content delta snaps
        // the reader straight back to the bottom they just left.
        shouldAutoScrollRef.current = false
      }
    },
    [messagesContainerRef, shouldAutoScrollRef]
  )

  useEffect(() => {
    const clearTraceFlash = () => {
      if (traceFlashTimerRef.current) clearTimeout(traceFlashTimerRef.current)
      traceFlashTimerRef.current = null
      for (const element of traceFlashTargetsRef.current) {
        element.classList.remove('turn-flash', 'trace-answer-flash')
      }
      traceFlashTargetsRef.current = []
    }

    const locateInConversation = (customEvent: Event) => {
      const detail = (customEvent as CustomEvent<ToolOutputTraceTarget>).detail
      const container = messagesContainerRef.current
      const target = findAnswerTraceTarget(container, detail)
      if (!target) return

      const selected = target.anchor
      const flashTargets = target.targets
      const offset =
        selected.getBoundingClientRect().top - (container?.getBoundingClientRect().top ?? 0)
      container?.scrollTo({ top: container.scrollTop + offset - 56, behavior: 'smooth' })
      if (selected.getAttribute('data-trace-call-id')) {
        const button = selected.querySelector<HTMLElement>("[role='button']")
        if (button && button.getAttribute('aria-expanded') === 'false') button.click()
      }
      const isAnswerBody = Boolean(selected.closest('[data-chat-assistant-output="true"]'))
      const flashClass = isAnswerBody ? 'trace-answer-flash' : 'turn-flash'

      clearTraceFlash()
      for (const element of flashTargets) {
        void element.offsetWidth
        element.classList.add(flashClass)
      }
      traceFlashTargetsRef.current = flashTargets
      traceFlashTimerRef.current = setTimeout(() => {
        for (const element of traceFlashTargetsRef.current) {
          element.classList.remove(flashClass)
        }
        traceFlashTargetsRef.current = []
        traceFlashTimerRef.current = null
      }, 2600)
      shouldAutoScrollRef.current = false
    }

    window.addEventListener('kagweb:trace-tool-output', locateInConversation)
    return () => {
      clearTraceFlash()
      window.removeEventListener('kagweb:trace-tool-output', locateInConversation)
    }
  }, [messagesContainerRef, shouldAutoScrollRef])
  /** Leave history and start following the live end of the turn again. */
  const resumeFollowingLatest = useCallback(() => {
    shouldAutoScrollRef.current = true
    scrollToBottom('instant')
  }, [scrollToBottom, shouldAutoScrollRef])

  /* A card waiting on the user is the one thing that MUST be on screen: the
     turn cannot continue until they act on it. Reading the question that
     precedes it normally scrolls up, which releases the streaming pin — so a
     quiz card would appear below the fold, under the composer, and the
     conversation looked stalled. Re-arm the pin and land on the card. */
  const awaitingUserReply = hasPendingAskUser(lastMessage?.events)
  // Read inside ``handleSend`` without adding a dependency that would rebuild
  // the callback (and so the composer) on every streamed event.
  const awaitingUserReplyRef = useRef(awaitingUserReply)
  awaitingUserReplyRef.current = awaitingUserReply
  useEffect(() => {
    if (!awaitingUserReply) return
    shouldAutoScrollRef.current = true
    // One frame later: the card has to be laid out before the bottom it
    // defines exists.
    const frame = requestAnimationFrame(() => scrollToBottom('instant'))
    return () => cancelAnimationFrame(frame)
  }, [awaitingUserReply, scrollToBottom, shouldAutoScrollRef])

  const copyAssistantMessage = useCallback(async (content: string) => {
    if (!content.trim()) return
    try {
      await navigator.clipboard.writeText(content)
    } catch (error) {
      console.error('Failed to copy assistant message:', error)
    }
  }, [])
  /* ---- URL-driven session loading ---- */

  const navigateToHome = useCallback(() => {
    router.replace('/chat', { scroll: false })
  }, [router])

  /** Abort in-flight load + navigate home. */
  const cancelSessionLoad = useCallback(() => {
    loadAbortRef.current?.abort()
    loadAbortRef.current = null
    setSessionLoading(false)
    setSessionLoadFailed(false)
    navigateToHome()
  }, [navigateToHome])

  /**
   * Shared helper: kick off a load. The user can cancel via the ✕ button.
   *
   * A session we already hold in memory is painted right away and refreshed
   * in the background — switching back to a conversation read earlier in this
   * visit costs nothing, and the overlay is reserved for the case where we
   * genuinely have nothing to show.
   *
   * The wait is bounded. A fetch that never settles used to leave the overlay
   * spinning forever with no way out but abandoning the conversation, and a
   * fetch that *failed* used to replace the URL with /chat — dropping the
   * session id, so a transient error read as "my history is gone". Both now
   * end in the same terminal, retryable state with the id still in the URL.
   */
  const startSessionLoad = useCallback(
    (sid: string) => {
      loadAbortRef.current?.abort()
      const ctrl = new AbortController()
      loadAbortRef.current = ctrl
      const cached = showCachedSession(sid)
      setSessionLoading(!cached)
      setSessionLoadFailed(false)

      // Aborting is how the timeout stops waiting, so it has to be
      // distinguishable from the user's ✕ and from a newer load taking over:
      // those two own the resulting state, a timeout does not.
      let timedOut = false
      const timeout = setTimeout(() => {
        timedOut = true
        ctrl.abort()
      }, SESSION_LOAD_TIMEOUT_MS)

      void loadSession(sid, { signal: ctrl.signal, revalidate: cached })
        .then(() => {
          clearTimeout(timeout)
          if (!ctrl.signal.aborted) {
            loadAbortRef.current = null
            setSessionLoading(false)
            // Settle at the bottom once the transcript is really laid out.
            // The layout-effect pin runs as the messages first render, when
            // lazily-loaded images (ChatMessages `loading="lazy"`) and the
            // `next/dynamic` capability viewers have not contributed their
            // heights yet, so its `scrollHeight` is short and the viewport
            // stops above the true bottom. One frame later those are in.
            //
            // Only on a cold open. A cached session is already painted at
            // the bottom and this resolves after a background revalidate —
            // re-arming there would yank a reader who had scrolled up.
            if (!cached) {
              shouldAutoScrollRef.current = true
              requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                  // A newer session may have superseded this one while the
                  // two frames elapsed; that load owns the viewport now.
                  if (!ctrl.signal.aborted) scrollToBottom('instant')
                })
              })
            }
          }
        })
        .catch(() => {
          clearTimeout(timeout)
          const surface = shouldSurfaceLoadFailure({
            aborted: ctrl.signal.aborted,
            timedOut,
            cached,
          })
          // A newer load (or the user's ✕) owns the state from here, and a
          // failed background refresh leaves the cached copy on screen.
          if (!surface) return
          loadAbortRef.current = null
          setSessionLoading(false)
          setSessionLoadFailed(true)
        })
    },
    [loadSession, showCachedSession, scrollToBottom, shouldAutoScrollRef]
  )

  const retrySessionLoad = useCallback(() => {
    if (sessionIdParam) startSessionLoad(sessionIdParam)
  }, [sessionIdParam, startSessionLoad])

  // Initial mount — load the session from the URL.
  // Uses a ref-based flag so Strict Mode double-mount doesn't break the flow:
  // when React tears down + re-mounts in dev, we reset initialLoadRef in
  // cleanup so the second mount restarts the load cleanly. The abort is
  // deliberately OMITTED from cleanup — cancelSessionLoad handles
  // user-initiated cancellation.
  useEffect(() => {
    if (initialLoadRef.current) return
    initialLoadRef.current = true
    if (sessionIdParam) {
      startSessionLoad(sessionIdParam)
    } else {
      newSession()
    }
    return () => {
      initialLoadRef.current = false
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // When URL param changes (sidebar navigation), load the corresponding session
  const prevSessionIdParam = useRef(sessionIdParam)
  useEffect(() => {
    if (sessionIdParam === prevSessionIdParam.current) return
    prevSessionIdParam.current = sessionIdParam
    // Abort any in-flight session load from the previous param
    loadAbortRef.current?.abort()
    loadAbortRef.current = null
    if (sessionIdParam) {
      if (sessionIdParam === state.sessionId) {
        setSessionLoading(false)
        setSessionLoadFailed(false)
        return
      }
      startSessionLoad(sessionIdParam)
    } else {
      newSession()
      setSessionLoading(false)
      setSessionLoadFailed(false)
    }
  }, [sessionIdParam, startSessionLoad, newSession, state.sessionId])

  // When a new session_id is assigned by the server, update the URL
  useEffect(() => {
    if (state.sessionId && !sessionIdParam) {
      router.replace(`/chat/${state.sessionId}`, { scroll: false })
    }
  }, [state.sessionId, sessionIdParam, router])

  useEffect(() => {
    setActiveSessionId(state.sessionId || sessionIdParam || null)
  }, [state.sessionId, sessionIdParam, setActiveSessionId])

  useEffect(() => {
    if (state.llmSelection || !activeLLMDefault) return
    setLLMSelection(activeLLMDefault)
  }, [activeLLMDefault, setLLMSelection, state.llmSelection])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const refresh = () => {
      void refreshLLMOptions({ force: true, background: true })
    }
    const refreshWhenVisible = () => {
      if (document.visibilityState === 'visible') refresh()
    }
    window.addEventListener('focus', refresh)
    window.addEventListener('pageshow', refresh)
    document.addEventListener('visibilitychange', refreshWhenVisible)
    return () => {
      window.removeEventListener('focus', refresh)
      window.removeEventListener('pageshow', refresh)
      document.removeEventListener('visibilitychange', refreshWhenVisible)
    }
  }, [refreshLLMOptions])

  /* Composer setup requested by the URL that opened this page. Runs once:
     from here on the composer is the user's to change. */
  const launchIntentAppliedRef = useRef(false)
  useEffect(() => {
    if (typeof window === 'undefined' || launchIntentAppliedRef.current) return
    const intent = readChatLaunchIntent(window.location.search)
    // Capability identity is backend-owned. Do not resolve a deep link against
    // the temporary chat-only fallback while the catalog request is in flight.
    if (intent.capability !== null && isCapabilityCatalogLoading) return
    launchIntentAppliedRef.current = true
    if (intent.capability !== null) handleSelectCapability(intent.capability)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isCapabilityCatalogLoading])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const t = e.target as Node
      if (
        capMenuRef.current &&
        !capMenuRef.current.contains(t) &&
        capBtnRef.current &&
        !capBtnRef.current.contains(t)
      )
        setCapMenuOpen(false)
      if (
        spaceMenuRef.current &&
        !spaceMenuRef.current.contains(t) &&
        spaceBtnRef.current &&
        !spaceBtnRef.current.contains(t)
      )
        setSpaceMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  /* ---- handlers ---- */

  const handleSelectCapability = useCallback(
    (value: string) => {
      const cap =
        capabilities.find(capability => capability.value === value) ??
        capabilities[0] ??
        getChatCapability('')
      setCapability(cap.value || null)
      setCapMenuOpen(false)
    },
    [capabilities, setCapability]
  )

  const fileToAttachment = fileToPendingAttachment

  const showAttachmentError = useCallback((message: string) => {
    setAttachmentError(message)
    if (attachmentErrorTimer.current) {
      clearTimeout(attachmentErrorTimer.current)
    }
    attachmentErrorTimer.current = setTimeout(() => {
      setAttachmentError(null)
      attachmentErrorTimer.current = null
    }, 4000)
  }, [])

  const filterAndReportFiles = useCallback(
    (files: File[]): File[] => {
      const { accepted, rejected } = selectAttachmentFiles(
        files,
        attachments.reduce((total, item) => total + (item.size ?? 0), 0),
        attachmentLimits
      )
      if (rejected.length) {
        const first = rejected[0]
        let msg: string
        if (first.reason === 'too_large') {
          msg = t('File too large: {{name}}', { name: first.name })
        } else if (first.reason === 'quota') {
          msg = t('Too many files, skipped some')
        } else {
          msg = t('Unsupported file type: {{name}}', { name: first.name })
        }
        showAttachmentError(msg)
      }
      return accepted
    },
    [attachments, attachmentLimits, showAttachmentError, t]
  )

  const handlePaste = useCallback(
    async (event: React.ClipboardEvent) => {
      const items = Array.from(event.clipboardData.items)
      const files = items
        .filter(item => item.kind === 'file')
        .map(item => item.getAsFile())
        .filter((f): f is File => f !== null)
      const accepted = filterAndReportFiles(files)
      if (!accepted.length) return
      event.preventDefault()
      const next = await Promise.all(accepted.map(fileToAttachment))
      setAttachments(prev => [...prev, ...next])
    },
    [fileToAttachment, filterAndReportFiles]
  )

  const removeAttachment = useCallback((index: number) => {
    setAttachments(prev => prev.filter((_, i) => i !== index))
  }, [])

  const handlePreviewPendingAttachment = useCallback(
    (index: number) => {
      const a = attachments[index]
      if (!a) return
      setPreviewSource({
        filename: a.filename,
        mimeType: a.mimeType,
        type: a.type,
        base64: a.base64,
        size: a.size,
      })
    },
    [attachments]
  )

  // Fold all messages once per state.messages change to power the
  // SessionActivityPanel on the right (tools, space refs, attachments).
  const sessionActivity = useMemo(
    () => buildSessionActivity(state.messages),
    [state.messages]
  )

  // Context-window readout for the composer chip: the newest turn that was
  // actually measured. Walking newest-first is what keeps the number steady
  // while a new turn streams — the in-flight assistant message has no result
  // event yet, so the walk falls through to the last completed turn and the
  // chip flips exactly once, when the new measurement lands.
  const contextBudget = useMemo(() => {
    for (let i = state.messages.length - 1; i >= 0; i -= 1) {
      const msg = state.messages[i]
      if (msg.role !== 'assistant') continue
      const budget = readContextBudget(msg.events)
      if (budget) return budget
    }
    return null
  }, [state.messages])

  // Clicking an attachment (from the Activity home or from a chat message)
  // routes into the panel as a new file tab. It auto-opens and the
  // preference is persisted so a follow-up click feels instant.
  const handlePreviewMessageAttachment = useCallback((a: MessageAttachment) => {
    viewerPanelRef.current?.openFileTab(a)
  }, [])

  // Event-delegated link interception inside the messages container. When
  // the user clicks an http(s) link in an assistant message, we open it as
  // a Viewer tab instead of letting the browser navigate / open a new tab.
  // Cmd/ctrl/shift + click keep their standard meaning (open in browser).
  const handleMessagesClick = useCallback((event: React.MouseEvent) => {
    if (event.defaultPrevented) return
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    if (event.button !== 0) return
    const target = event.target as HTMLElement | null
    if (!target) return
    const anchor = target.closest<HTMLAnchorElement>('a[href]')
    if (!anchor) return
    const href = anchor.getAttribute('href')
    if (!href) return
    if (!/^https?:\/\//i.test(href)) return
    event.preventDefault()
    viewerPanelRef.current?.openWebTab(href)
  }, [])

  const handleMessagesCopy = useCallback(
    (event: React.ClipboardEvent<HTMLDivElement>) => {
      const selection = window.getSelection()
      const container = messagesContainerRef.current
      if (!selection || !container || selection.isCollapsed) return
      if (
        !selection.rangeCount ||
        !container.contains(selection.getRangeAt(0).commonAncestorContainer)
      ) {
        return
      }
      const remapped = textFromDomSelection(selection)
      const raw = normalizeSelectedText(selection.toString())
      if (!remapped || remapped === raw) return
      event.clipboardData.setData('text/plain', remapped)
      event.preventDefault()
    },
    [messagesContainerRef]
  )

  const handleClosePreview = useCallback(() => {
    setPreviewSource(null)
  }, [])

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current += 1
    if (e.dataTransfer.types.includes('Files')) setDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    dragCounter.current -= 1
    if (dragCounter.current === 0) setDragging(false)
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      setDragging(false)
      dragCounter.current = 0
      const accepted = filterAndReportFiles(Array.from(e.dataTransfer.files))
      if (!accepted.length) return
      const next = await Promise.all(accepted.map(fileToAttachment))
      setAttachments(prev => [...prev, ...next])
    },
    [fileToAttachment, filterAndReportFiles]
  )

  const handleAddFiles = useCallback(
    async (files: File[]) => {
      const accepted = filterAndReportFiles(files)
      if (!accepted.length) return
      const next = await Promise.all(accepted.map(fileToAttachment))
      setAttachments(prev => [...prev, ...next])
    },
    [fileToAttachment, filterAndReportFiles]
  )

  const handleSend = useCallback(
    async (content: string) => {
      // A turn paused on a question: what the user typed is their answer, not
      // a new message. Routing it here means the card is one way to answer,
      // not the only one — and a card that never rendered no longer strands
      // the learner with a turn they can only cancel.
      if (awaitingUserReplyRef.current) {
        if (content.trim()) submitUserReply({ text: content })
        return
      }
      if ((!content && !attachments.length && !selectedHistorySessions.length) || state.isStreaming)
        return

      const extraAttachments = attachments.map(a => ({
        type: a.type,
        filename: a.filename,
        base64: a.base64,
        mime_type: a.mimeType,
      }))

      const messageContent =
        content ||
        (selectedHistorySessions.length
          ? t('Please use the selected context to help with this request.')
          : '') ||
        (attachments.some(a => a.type === 'image')
          ? t('Please analyze the attached image(s).')
          : '')
      sendMessage(messageContent, extraAttachments, undefined, historyReferencesPayload)
      shouldAutoScrollRef.current = true
      setAttachments([])
      setSelectedHistorySessions([])
    },
    [
      attachments,
      historyReferencesPayload,
      selectedHistorySessions.length,
      sendMessage,
      shouldAutoScrollRef,
      state.isStreaming,
      submitUserReply,
      t,
    ]
  )

  const handleRegenerateMessage = useCallback(() => {
    regenerateLastMessage()
  }, [regenerateLastMessage])

  const handleSelectHistoryPicker = useCallback(() => {
    setShowHistoryPicker(true)
  }, [])
  const handleRemoveHistory = useCallback((sessionId: string) => {
    setSelectedHistorySessions(prev => prev.filter(item => item.sessionId !== sessionId))
  }, [])
  const handleCloseHistoryPicker = useCallback(() => {
    setShowHistoryPicker(false)
  }, [])
  const handleApplyHistorySessions = useCallback((sessions: SelectedHistorySession[]) => {
    setSelectedHistorySessions(sessions)
  }, [])

  const handleDownloadMarkdown = useCallback(() => {
    if (!state.messages.length) return
    const title =
      state.messages
        .find(msg => msg.role === 'user')
        ?.content.trim()
        .slice(0, 80) || 'Chat Session'
    downloadChatMarkdown(state.messages, { title })
  }, [state.messages])

  return (
    <div className="relative h-full overflow-hidden">
      <div
        // When the preview drawer is open AND the viewport is wide enough,
        // push the chat content to the left by the drawer's width so the two
        // panels live side-by-side (matches Claude desktop). On smaller
        // screens the drawer overlays — squeezing a phone-width chat into
        // the remaining ~30 px would be useless. The actual padding +
        // transition lives in `chat-preview-shell` (globals.css) so we can
        // hand-tune it without fighting Tailwind's arbitrary-value parser.
        data-preview-open={previewSource ? "true" : "false"}
        data-viewer-open={viewerPanelOpen || sessionDagOpen ? "true" : "false"}
        className="chat-preview-shell flex h-full flex-col overflow-hidden bg-[var(--background)]"
      >
        <div className="mx-auto flex w-full max-w-[960px] flex-wrap items-center justify-between gap-x-3 gap-y-1.5 px-6 pt-3 pb-0">
          <div className="group/title min-w-0 flex flex-1 items-center gap-2">
            {sessionTitleEditing ? (
              <input
                ref={titleInputRef}
                value={sessionTitleDraft}
                onChange={event => setSessionTitleDraft(event.target.value)}
                onBlur={() => void commitSessionTitleEdit()}
                onKeyDown={handleSessionTitleKeyDown}
                disabled={sessionTitleSaving}
                aria-label={t('Session title')}
                className="min-w-0 flex-1 rounded-xl border border-[var(--border)] bg-[var(--background)] px-3 py-1.5 font-serif text-[17px] font-semibold tracking-[-0.01em] text-[var(--foreground)] shadow-sm outline-none transition focus:border-[var(--ring)] focus:ring-2 focus:ring-[var(--ring)]/20 disabled:opacity-60"
                maxLength={100}
              />
            ) : (
              <button
                type="button"
                onClick={startSessionTitleEdit}
                disabled={!canRenameSession}
                title={
                  canRenameSession
                    ? t('Click to rename session')
                    : t('Start a conversation to rename')
                }
                className="inline-flex min-w-0 max-w-full items-center gap-2 rounded-xl px-2 py-1 text-left font-serif text-[17px] font-semibold tracking-[-0.01em] text-[var(--foreground)] transition hover:bg-[var(--muted)]/55 disabled:cursor-default disabled:hover:bg-transparent"
              >
                <span className="truncate">{displaySessionTitle}</span>
                {canRenameSession ? (
                  <PenLine className="h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)] opacity-0 transition-opacity group-hover/title:opacity-100" />
                ) : null}
              </button>
            )}
            {sessionTitleSaving ? (
              <span className="shrink-0 text-xs text-[var(--muted-foreground)]">
                {t('Saving...')}
              </span>
            ) : null}
            {sessionTitleError ? (
              <span className="shrink-0 text-xs text-[var(--destructive)]">
                {sessionTitleError}
              </span>
            ) : null}
          </div>
          <div className="flex shrink-0 items-center gap-0.5">
            <HeaderActionButton
              onClick={handleDownloadMarkdown}
              disabled={!state.messages.length}
              icon={Download}
              label={t('Download Markdown')}
              title={t('Download chat history as Markdown')}
            />
            <HeaderActionButton
              onClick={() => viewerPanelRef.current?.openMarkdownNoteTab()}
              icon={NotebookPen}
              label={t('Markdown note')}
              title={t('Write Markdown in chat')}
            />
            <HeaderActionButton
              onClick={() => {
                if (viewerPanelOpen && sessionDagOpen) {
                  setViewerOpen(false);
                } else {
                  setSessionDagOpen(true);
                  setViewerOpen(true);
                  viewerPanelRef.current?.focusSessionDag();
                }
              }}
              active={viewerPanelOpen && sessionDagOpen}
              icon={Network}
              label={t('Session DAG')}
              title={t('View the whole conversation as a trace graph')}
            />
            <HeaderActionButton
              onClick={() => setTraceMode(traceMode === 'learner' ? 'expert' : 'learner')}
              active={traceMode === 'expert'}
              icon={traceMode === 'expert' ? Terminal : GraduationCap}
              label={traceMode === 'expert' ? t('Expert view') : t('Learner view')}
              title={
                traceMode === 'expert'
                  ? t('Trace detail: Expert mode')
                  : t('Trace detail: Learner mode')
              }
            />
            <HeaderActionButton
              onClick={() => {
                if (viewerPanelOpen && !sessionDagOpen) toggleViewerPanel();
                else ensureActivityPanelOpen();
              }}
              active={viewerPanelOpen && !sessionDagOpen}
              icon={PanelRight}
              label={t('Activity')}
              title={t('Session activity, attachments & previews')}
            />
          </div>
        </div>
        <div className="flex w-full flex-1 min-h-0 flex-col">
          {sessionLoading || sessionLoadFailed ? (
            <div className="flex w-full flex-1 min-h-0 justify-center px-6">
              <div className="h-full w-full max-w-[960px]">
                <SessionLoadingView
                  onCancel={cancelSessionLoad}
                  failed={sessionLoadFailed}
                  onRetry={retrySessionLoad}
                />
              </div>
            </div>
          ) : !hasMessages ? (
            <div className="flex w-full flex-1 min-h-0 items-end justify-center pb-14 animate-fade-in px-6">
              <div className="w-full max-w-[960px] flex items-center justify-center gap-4">
                <img
                  src="/logo_black.png"
                  alt="学研助手"
                  width={40}
                  height={40}
                  className="h-10 w-10 select-none"
                  draggable={false}
                />
                <h1 className="font-serif text-[40px] font-medium leading-[1.1] tracking-[-0.015em] text-[var(--foreground)]">
                  {t(welcomeGreeting)}
                </h1>
              </div>
            </div>
          ) : (
            // Positioned wrapper spanning exactly the scrollport, so the
            // turn navigator can overlay the left gutter without living
            // inside the masked scroll container (its top/bottom fade
            // would clip the rail's ends).
            <div className="relative flex w-full flex-1 min-h-0 flex-col">
              <div
                ref={messagesContainerRef}
                data-chat-scroll-root="true"
                onScroll={handleMessagesScroll}
                onClick={handleMessagesClick}
                onCopy={handleMessagesCopy}
                // `both-edges` reserves the scrollbar gutter on both sides so
                // the inner mx-auto column centers on the same axis as the
                // header and composer (siblings outside this scrollport) on
                // classic-scrollbar platforms; plain `stable` would shift it
                // ~half a scrollbar-width left of them.
                className={`w-full flex-1 min-h-0 overflow-y-auto [scrollbar-gutter:stable_both-edges] ${hasMessages ? 'pt-6' : 'pt-2 pb-6'}`}
                style={
                  hasMessages
                    ? (() => {
                        // The bottom 40 px of the messages area fades to
                        // transparent so content "dissolves" into the composer
                        // gutter. Without enough bottom padding, the fade
                        // overlaps the last assistant paragraph and looks like
                        // a stuck scroll — the user reaches scrollHeight but
                        // can still see only a faded sliver of text. paddingBottom
                        // is sized so the fade falls over empty space.
                        const maskImage =
                          'linear-gradient(to bottom, transparent 0px, #000 32px, #000 calc(100% - 40px), transparent 100%)'
                        return {
                          paddingBottom: '48px',
                          WebkitMaskImage: maskImage,
                          maskImage,
                        }
                      })()
                    : undefined
                }
              >
                <div
                  data-chat-column="true"
                  className="mx-auto w-full max-w-[960px] space-y-9 px-6"
                >
                  <ChatMessageList
                    messages={state.messages}
                    isStreaming={state.isStreaming}
                    sessionId={state.sessionId}
                    onCopyAssistantMessage={copyAssistantMessage}
                    onRegenerateMessage={handleRegenerateMessage}
                    onPreviewAttachment={handlePreviewMessageAttachment}
                    onDeleteTurn={deleteTurn}
                    selectedBranches={state.selectedBranches}
                    onEditMessage={editMessage}
                    onSwitchBranch={switchBranch}
                    onSubmitUserReply={submitUserReply}
                  />
                  <div ref={messagesEndRef} className="h-px w-full shrink-0" />
                </div>
              </div>
              <TurnNavigator
                entries={chatOutline}
                scrollRootRef={messagesContainerRef}
                onJump={jumpToTurn}
                onJumpToBottom={resumeFollowingLatest}
              />
            </div>
          )}

          <ChatComposer
            composerRef={composerRef}
            capMenuRef={capMenuRef}
            capBtnRef={capBtnRef}
            spaceMenuRef={spaceMenuRef}
            spaceBtnRef={spaceBtnRef}
            dragCounter={dragCounter}
            dragging={dragging}
            capMenuOpen={capMenuOpen}
            spaceMenuOpen={spaceMenuOpen}
            hasMessages={hasMessages}
            attachments={attachments}
            attachmentError={attachmentError}
            activeCap={activeCap}
            llmOptions={llmOptions}
            modelSelectorEnabled={auth.modelSelectorEnabled}
            activeLLMDefault={activeLLMDefault}
            llmSelection={state.llmSelection}
            llmOptionsLoading={llmOptionsLoading}
            llmOptionsError={llmOptionsError}
            onRefreshLLMOptions={() => void refreshLLMOptions({ force: true })}
            contextBudget={contextBudget}
            selectedHistorySessions={selectedHistorySessions}
            isStreaming={state.isStreaming}
            capabilities={visibleCapabilities}
            onSetCapMenuOpen={setCapMenuOpen}
            onSetSpaceMenuOpen={setSpaceMenuOpen}
            onSelectLLM={setLLMSelection}
            onSelectHistoryPicker={handleSelectHistoryPicker}
            onSend={handleSend}
            awaitingUserReply={awaitingUserReply}
            onRemoveAttachment={removeAttachment}
            onPreviewAttachment={handlePreviewPendingAttachment}
            onRemoveHistory={handleRemoveHistory}
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            onPaste={handlePaste}
            onAddFiles={handleAddFiles}
            onSelectCapability={handleSelectCapability}
            onCancelStreaming={cancelStreamingTurn}
            prefillInputRef={prefillInputRef}
          />
          <div
            aria-hidden="true"
            className="shrink-0"
            style={{
              flexGrow: hasMessages ? 0 : 1.4,
              transition: 'flex-grow 650ms cubic-bezier(0.16, 1, 0.3, 1)',
            }}
          />
        </div>
        <HistorySessionPicker
          open={showHistoryPicker}
          onClose={handleCloseHistoryPicker}
          onApply={handleApplyHistorySessions}
        />
        <FilePreviewDrawer
          open={previewSource !== null}
          source={previewSource}
          onClose={handleClosePreview}
        />
        <SessionViewerPanel
          ref={viewerPanelRef}
          open={viewerPanelOpen && previewSource === null}
          sessionId={state.sessionId}
          activity={sessionActivity}
          sessionDag={
            <SessionDagPanel
              open={viewerPanelOpen && sessionDagOpen}
              messages={state.messages}
              selectedBranches={state.selectedBranches}
              sessionId={state.sessionId ?? undefined}
              onLocateMessage={(messageId) => jumpToTurn(`m${messageId}`)}
            />
          }
          sessionDagActive={sessionDagOpen}
          onSessionDagActiveChange={setSessionDagOpen}
          onClose={() => setViewerOpen(false)}
          onAutoOpen={() => setViewerOpen(true)}
          onTraceToolOutput={options => {
            window.dispatchEvent(
              new CustomEvent('kagweb:trace-tool-output', { detail: options })
            )
          }}
        />
      </div>
    </div>
  )
}

/**
 * Header action button that auto-collapses to icon-only when the chat
 * column gets squeezed (Viewer panel open, narrow viewport, etc.). The
 * label stays as the button's `title` so hovering an icon still reveals
 * what it does. Optional `active` flag paints the button with a primary
 * tint, used by the panel-toggle buttons to surface their on/off state.
 */
// Claude-style icon-only header action: bare 16px glyph, function revealed
// by an instant tooltip; active state gets a primary tint.
function HeaderActionButton({
  onClick,
  disabled,
  active,
  icon: Icon,
  label,
  title,
}: {
  onClick: () => void
  disabled?: boolean
  active?: boolean
  icon: LucideIcon
  label: string
  title?: string
}) {
  return (
    <Tooltip label={title ?? label} side="bottom">
      <button
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        aria-pressed={active}
        className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-[background-color,color,transform] duration-150 active:scale-90 disabled:cursor-not-allowed disabled:opacity-40 ${
          active
            ? 'bg-[var(--primary)]/10 text-[var(--primary)]'
            : 'text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)] disabled:hover:bg-transparent disabled:hover:text-[var(--muted-foreground)]'
        }`}
      >
        <Icon size={16} strokeWidth={1.7} className="shrink-0" />
      </button>
    </Tooltip>
  )
}
