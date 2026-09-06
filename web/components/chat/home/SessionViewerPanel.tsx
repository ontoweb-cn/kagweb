'use client'

import { browserStorage } from '@/shared/storage'

/**
 * SessionViewerPanel — full right-side sidebar with browser-style tabs that
 * can hold (a) attachment previews and (b) embedded web pages clicked from
 * assistant messages.
 *
 * - Tabs across the top of the panel; each closeable.
 * - File tabs use the same lazy previewer set as FilePreviewDrawer.
 * - Web tabs render an iframe of the URL. Cross-origin frames may refuse
 *   to load — we expose an "Open in browser" affordance so the user can
 *   always fall back. The user's network ultimately decides what loads.
 * - Imperative API via ref: openFileTab(att), openWebTab(url).
 */

import {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import dynamic from 'next/dynamic'
import {
  Activity,
  AlertCircle,
  ArrowRight,
  Download,
  ExternalLink,
  FileText,
  FileUp,
  Globe,
  LocateFixed,
  Loader2,
  NotebookPen,
  Network,
  Paperclip,
  Share2,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  previewKindFor,
  resolveSourceUrl,
  type FilePreviewSource,
} from '@/components/chat/preview/previewerFor'
import { ActivityBody, type SessionActivity } from '@/components/chat/home/SessionActivityPanel'
import GraphSubgraphView from './GraphSubgraphView'
import {
  buildToolTraceTerms,
  collectSessionGraphs,
  collectSessionSources,
  type SessionSourceEntry,
  type ToolOutputTraceTarget,
} from '@/lib/session-activity'
import { apiUrl } from '@/lib/api'
import type { MessageAttachment } from '@/features/chat/ChatStateAdapter'
import { listKnowledgeBaseFiles, type KnowledgeBaseFile } from '@/features/knowledge/api/files'
import { knowledgeBaseFileRoute } from '@/lib/resource-routes'

const PdfPreview = dynamic(() => import('@/components/chat/preview/previewers/PdfPreview'))
const ImagePreview = dynamic(() => import('@/components/chat/preview/previewers/ImagePreview'))
const SvgPreview = dynamic(() => import('@/components/chat/preview/previewers/SvgPreview'))
const MarkdownPreview = dynamic(
  () => import('@/components/chat/preview/previewers/MarkdownPreview')
)
const TextPreview = dynamic(() => import('@/components/chat/preview/previewers/TextPreview'))
const DocxPreview = dynamic(() => import('@/components/chat/preview/previewers/DocxPreview'))
const XlsxPreview = dynamic(() => import('@/components/chat/preview/previewers/XlsxPreview'))
const OfficeTextPreview = dynamic(
  () => import('@/components/chat/preview/previewers/OfficeTextPreview')
)
const FallbackPreview = dynamic(
  () => import('@/components/chat/preview/previewers/FallbackPreview')
)
const ChatMarkdownNoteTabBody = dynamic(
  () => import('@/components/chat/home/ChatMarkdownNoteTab'),
  { ssr: false }
)

const ANIM_MS = 220

/* Resizable width — the panel overlays from the right and the chat shell
   reserves space for it via the ``--viewer-width`` CSS var (see globals.css).
   Both read the same var so the squeeze and the panel edge stay locked
   together while dragging. */
const VIEWER_WIDTH_VAR = '--viewer-width'
const VIEWER_WIDTH_KEY = 'dt:viewer-width'
const VIEWER_WIDTH_DEFAULT = 620
const VIEWER_WIDTH_MIN = 400
const VIEWER_WIDTH_MAX = 960

function clampViewerWidth(px: number): number {
  // Hard floor/ceiling, plus a soft ceiling that always leaves the chat
  // column ~30% of the viewport so the panel can't swallow the conversation.
  const ceiling =
    typeof window !== 'undefined'
      ? Math.max(VIEWER_WIDTH_MIN, Math.min(VIEWER_WIDTH_MAX, window.innerWidth * 0.7))
      : VIEWER_WIDTH_MAX
  return Math.round(Math.max(VIEWER_WIDTH_MIN, Math.min(px, ceiling)))
}

function readStoredViewerWidth(): number {
  if (typeof window === 'undefined') return VIEWER_WIDTH_DEFAULT
  const raw = browserStorage.readRaw('local', VIEWER_WIDTH_KEY)
  const parsed = raw ? Number(raw) : NaN
  return Number.isFinite(parsed) ? clampViewerWidth(parsed) : VIEWER_WIDTH_DEFAULT
}

/* ------------------------------------------------------------------ */
/*  Tab types                                                          */
/* ------------------------------------------------------------------ */

type ViewerTab =
  | { kind: 'file'; id: string; label: string; source: FilePreviewSource }
  | { kind: 'web'; id: string; label: string; url: string }
  | { kind: 'markdown-note'; id: string; label: string }

export interface SessionViewerPanelHandle {
  openFileTab(a: MessageAttachment): void
  openWebTab(url: string): void
  /** Opens the session-scoped inline Markdown editor tab. */
  openMarkdownNoteTab(): void
  /** Opens the panel and switches to the Activity home (where the
   *  capability-config card lives). */
  focusActivityHome(): void;
  /** Opens the shared viewer on the session reasoning graph. */
  focusSessionDag(): void;
}

interface SessionViewerPanelProps {
  open: boolean
  sessionId: string | null
  onClose: () => void
  onAutoOpen: () => void
  /** Aggregated session activity, shown on the Activity home view. */
  activity: SessionActivity
  sessionDag?: ReactNode;
  sessionDagActive?: boolean;
  onSessionDagActiveChange?: (active: boolean) => void;
  /** Jump the transcript to the tool exchange that produced an activity row. */
  onTraceToolOutput?: (options: ToolOutputTraceTarget) => void
}

function fileTabIdFor(a: MessageAttachment, fallback: number): string {
  return `file:${a.id ?? a.filename ?? `idx-${fallback}`}`
}

function webTabIdFor(url: string): string {
  return `web:${url}`
}

const markdownNoteTabId = 'markdown-note'

function hostnameFor(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url.slice(0, 32)
  }
}

function attachmentToPreviewSource(a: MessageAttachment): FilePreviewSource {
  return {
    filename: a.filename || '',
    mimeType: a.mime_type,
    type: a.type,
    url: a.url,
    base64: a.base64,
    extractedText: a.extracted_text,
    id: a.id,
  }
}

/* ------------------------------------------------------------------ */
/*  Panel                                                              */
/* ------------------------------------------------------------------ */

function SessionViewerPanelInner(
  {
    open,
    sessionId,
    onClose,
    onAutoOpen,
    activity,
    sessionDag,
    sessionDagActive,
    onSessionDagActiveChange,
    onTraceToolOutput,
  }: SessionViewerPanelProps,
  ref: React.Ref<SessionViewerPanelHandle>
) {
  const { t } = useTranslation()
  const [tabs, setTabs] = useState<ViewerTab[]>([])
  const [activeTabId, setActiveTabId] = useState<string | null>(null)

  // Drag-to-resize width. The width is NOT React state — the panel reads it
  // from the ``--viewer-width`` CSS var (so does the chat shell's squeeze),
  // and the drag writes that var directly. This keeps the inline style a
  // constant string (no SSR/client hydration mismatch) and means a drag
  // re-styles one DOM node per frame instead of re-rendering the whole panel
  // every pointer move — the difference between janky and buttery.
  const widthRef = useRef(VIEWER_WIDTH_DEFAULT)
  useEffect(() => {
    // Restore the persisted width after mount (kept out of the initial render
    // so server and client agree on the fallback width).
    widthRef.current = readStoredViewerWidth()
    document.documentElement.style.setProperty(VIEWER_WIDTH_VAR, `${widthRef.current}px`)
  }, [])

  const startResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault()
    document.documentElement.dataset.viewerResizing = 'true'
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'

    let rafId = 0
    let pendingX = e.clientX
    const apply = () => {
      rafId = 0
      const w = clampViewerWidth(window.innerWidth - pendingX)
      widthRef.current = w
      document.documentElement.style.setProperty(VIEWER_WIDTH_VAR, `${w}px`)
    }
    const onMove = (ev: PointerEvent) => {
      // Coalesce to one var write per frame — pointermove can fire faster
      // than the display refreshes.
      pendingX = ev.clientX
      if (!rafId) rafId = requestAnimationFrame(apply)
    }
    const onUp = () => {
      if (rafId) cancelAnimationFrame(rafId)
      delete document.documentElement.dataset.viewerResizing
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      browserStorage.writeRaw('local', VIEWER_WIDTH_KEY, String(widthRef.current))
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
  }, [])

  // Wipe tabs whenever the session changes — preview/web state belongs to
  // the conversation that triggered it.
  const [trackedSessionId, setTrackedSessionId] = useState<string | null>(sessionId)
  if (trackedSessionId !== sessionId) {
    setTrackedSessionId(sessionId)
    setTabs([])
    setActiveTabId(null)
  }

  const openFileTab = useCallback(
    (a: MessageAttachment) => {
      setTabs(prev => {
        const id = fileTabIdFor(a, prev.length)
        const existingIdx = prev.findIndex(tab => tab.id === id)
        if (existingIdx >= 0) {
          setActiveTabId(id)
          return prev
        }
        const label = a.filename || 'Attachment'
        const next: ViewerTab = {
          kind: 'file',
          id,
          label,
          source: attachmentToPreviewSource(a),
        }
        setActiveTabId(id)
        return [...prev, next]
      })
      onAutoOpen()
    },
    [onAutoOpen]
  )

  const openWebTab = useCallback(
    (url: string) => {
      setTabs(prev => {
        const id = webTabIdFor(url)
        const existingIdx = prev.findIndex(tab => tab.id === id)
        if (existingIdx >= 0) {
          setActiveTabId(id)
          return prev
        }
        const next: ViewerTab = {
          kind: 'web',
          id,
          label: hostnameFor(url),
          url,
        }
        setActiveTabId(id)
        return [...prev, next]
      })
      onAutoOpen()
    },
    [onAutoOpen]
  )

  const openMarkdownNoteTab = useCallback(() => {
    setTabs(prev => {
      const existingIdx = prev.findIndex(tab => tab.id === markdownNoteTabId)
      if (existingIdx >= 0) {
        setActiveTabId(markdownNoteTabId)
        return prev
      }
      const next: ViewerTab = {
        kind: 'markdown-note',
        id: markdownNoteTabId,
        label: t('Markdown note'),
      }
      setActiveTabId(markdownNoteTabId)
      return [...prev, next]
    })
    onAutoOpen()
  }, [onAutoOpen, t])

  // Open the panel and return to the Activity home (where the
  // capability-config card surfaces). Used by the send-gate.
  const focusActivityHome = useCallback(() => {
    setActiveTabId(null);
    onSessionDagActiveChange?.(false);
    onAutoOpen();
  }, [onAutoOpen, onSessionDagActiveChange]);

  const focusSessionDag = useCallback(() => {
    setActiveTabId(null);
    onSessionDagActiveChange?.(true);
    onAutoOpen();
  }, [onAutoOpen, onSessionDagActiveChange]);

  useImperativeHandle(
    ref,
    () => ({
      openFileTab,
      openWebTab,
      openMarkdownNoteTab,
      focusActivityHome,
      focusSessionDag,
    }),
    [
      openFileTab,
      openWebTab,
      openMarkdownNoteTab,
      focusActivityHome,
      focusSessionDag,
    ],
  );

  const closeTab = useCallback(
    (id: string) => {
      setTabs(prev => {
        const idx = prev.findIndex(tab => tab.id === id)
        if (idx === -1) return prev
        const next = prev.filter(tab => tab.id !== id)
        if (activeTabId === id) {
          // Fall back to the previous tab, or to the Activity home when none
          // remain — the panel stays open since the home is always useful.
          setActiveTabId(next.length === 0 ? null : (next[Math.max(0, idx - 1)] ?? next[0]).id)
        }
        return next
      })
    },
    [activeTabId]
  )

  // ESC closes the panel.
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // The viewer is visible whenever it's open — even with no tabs. The
  // tabs.length === 0 case renders a "Landing" page where the user can
  // paste a URL or pick a local file to open as the first tab.
  const visible = open
  const activeTab = tabs.find(tab => tab.id === activeTabId) ?? null

  const openLocalFile = useCallback(
    (file: File) => {
      const url = URL.createObjectURL(file)
      openFileTab({
        type: file.type.startsWith('image/') ? 'image' : 'file',
        filename: file.name,
        mime_type: file.type,
        url,
      })
    },
    [openFileTab]
  )

  /* Below the drawer breakpoint the panel is a full-screen sheet, not a side
     panel: there is no chat column left to sit beside, and VIEWER_WIDTH_MIN is
     wider than the phone it would overlay. That override lives in CSS
     (`max-md:!w-full`) rather than `useDevice()` so it is right on the first
     paint and follows an orientation change for free — the var-driven width
     and its drag handle stay desktop-only machinery. */
  return (
    <div
      role="dialog"
      aria-hidden={!visible}
      className={`fixed right-0 top-[var(--top-banner-h)] z-40 flex h-[calc(100dvh-var(--top-banner-h))] flex-col border-l border-[var(--border)] bg-[var(--card)] transition-transform ease-out max-md:!w-full md:max-w-[92vw] ${
        // shadow-2xl only while visible — when closed, translate-x-full moves
        // the box off-screen but its blurred shadow still bleeds ~38px back
        // onto the viewport's right edge. Dropping the shadow off-screen kills
        // that stray sliver.
        visible ? 'translate-x-0 shadow-2xl' : 'translate-x-full'
      }`}
      style={{
        // Constant string (not a state value) so SSR and the first client
        // render agree; the real width lives in the var, updated imperatively.
        width: `var(${VIEWER_WIDTH_VAR}, ${VIEWER_WIDTH_DEFAULT}px)`,
        willChange: 'transform',
        transitionDuration: `${ANIM_MS}ms`,
        pointerEvents: visible ? 'auto' : 'none',
      }}
    >
      {/* Left-edge resize handle. A wide invisible hit-area with a hairline
          that tints on hover/drag — drag left/right to set the panel width. */}
      <div
        onPointerDown={startResize}
        role="separator"
        aria-orientation="vertical"
        aria-label={t('Resize viewer')}
        className="group/resize absolute left-0 top-0 z-10 h-full w-2 -translate-x-1/2 cursor-col-resize max-md:hidden"
      >
        <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-transparent transition-colors group-hover/resize:bg-[var(--primary)]/40" />
      </div>
      <TabBar
        tabs={tabs}
        activeTabId={activeTabId}
        homeActive={activeTab === null && !sessionDagActive}
        dagActive={activeTab === null && Boolean(sessionDagActive)}
        onSelectHome={() => {
          setActiveTabId(null);
          onSessionDagActiveChange?.(false);
        }}
        onSelectDag={() => {
          setActiveTabId(null);
          onSessionDagActiveChange?.(true);
        }}
        onSelect={setActiveTabId}
        onCloseTab={closeTab}
        onClosePanel={onClose}
      />
      <div className="relative flex-1 overflow-hidden bg-[var(--card)]">
        {activeTab?.kind === 'file' ? (
          <FileTabBody source={activeTab.source} />
        ) : activeTab?.kind === 'web' ? (
          <WebTabBody key={activeTab.url} url={activeTab.url} />
        ) : activeTab?.kind === 'markdown-note' ? (
          <ChatMarkdownNoteTabBody
            key={`${activeTab.id}:${sessionId ?? 'pending'}`}
            sessionId={sessionId}
          />
        ) : sessionDagActive ? (
          sessionDag
        ) : (
          <ActivityHome
            activity={activity}
            open={visible}
            onOpenAttachment={openFileTab}
            onOpenWebTab={openWebTab}
            onOpenLocalFile={openLocalFile}
            onTraceToolOutput={onTraceToolOutput}
          />
        )}
      </div>
    </div>
  )
}

const SessionViewerPanel = memo(forwardRef(SessionViewerPanelInner))
export default SessionViewerPanel

/* ------------------------------------------------------------------ */
/*  Tab bar                                                            */
/* ------------------------------------------------------------------ */

/**
 * Chrome-style tab bar. The strip itself is a muted band; the active tab
 * "lifts" out of it in the page-body colour with rounded top corners — so
 * the active tab and the body underneath read as one continuous surface,
 * exactly like a browser tab. No coloured top stripe; inactive tabs stay
 * transparent over the strip.
 */
function TabBar({
  tabs,
  activeTabId,
  homeActive,
  dagActive,
  onSelectHome,
  onSelectDag,
  onSelect,
  onCloseTab,
  onClosePanel,
}: {
  tabs: ViewerTab[];
  activeTabId: string | null;
  homeActive: boolean;
  dagActive: boolean;
  onSelectHome: () => void;
  onSelectDag: () => void;
  onSelect: (id: string) => void;
  onCloseTab: (id: string) => void;
  onClosePanel: () => void;
}) {
  const { t } = useTranslation()
  return (
    <div className="flex shrink-0 items-end gap-2 bg-[color-mix(in_srgb,var(--muted)_40%,var(--background))] px-2 pt-2 pb-0">
      <div className="flex min-w-0 flex-1 items-end gap-[2px] overflow-x-auto">
        {/* Persistent Activity home — always first, never closeable. It's the
            session-activity landing; opening a file/web tab focuses that tab
            and this recedes, browser-home-tab style. */}
        <button
          type="button"
          onClick={onSelectHome}
          className={`inline-flex shrink-0 items-center gap-1.5 rounded-t-md py-1.5 pl-2.5 pr-3 text-[11.5px] font-medium transition-colors ${
            homeActive
              ? 'bg-[var(--card)] text-[var(--foreground)]'
              : 'bg-transparent text-[var(--muted-foreground)] hover:bg-[color-mix(in_srgb,var(--card)_70%,transparent)] hover:text-[var(--foreground)]'
          }`}
          title={t('Activity')}
        >
          <Activity size={11} strokeWidth={1.9} className="shrink-0" />
          <span>{t('Activity')}</span>
        </button>
        <button
          type="button"
          onClick={onSelectDag}
          className={`inline-flex shrink-0 items-center gap-1.5 rounded-t-md py-1.5 pl-2.5 pr-3 text-[11.5px] font-medium transition-colors ${
            dagActive
              ? "bg-[var(--card)] text-[var(--foreground)]"
              : "bg-transparent text-[var(--muted-foreground)] hover:bg-[color-mix(in_srgb,var(--card)_70%,transparent)] hover:text-[var(--foreground)]"
          }`}
          aria-pressed={dagActive}
        >
          <Network size={11} strokeWidth={1.9} className="shrink-0" />
          {t("Session DAG")}
        </button>
        {tabs.map((tab) => {
          const active = tab.id === activeTabId;
          const Icon =
            tab.kind === 'web'
              ? Globe
              : tab.kind === 'markdown-note'
                ? NotebookPen
                : Paperclip
          return (
            <div
              key={tab.id}
              className={`group inline-flex max-w-[180px] shrink-0 items-center rounded-t-md text-[11.5px] font-medium transition-colors ${
                active
                  ? 'bg-[var(--card)] text-[var(--foreground)]'
                  : 'bg-transparent text-[var(--muted-foreground)] hover:bg-[color-mix(in_srgb,var(--card)_70%,transparent)] hover:text-[var(--foreground)]'
              }`}
              title={tab.label}
            >
              <button
                type="button"
                onClick={() => onSelect(tab.id)}
                className="inline-flex min-w-0 flex-1 items-center gap-1.5 py-1.5 pl-2.5 pr-1 text-left"
              >
                <Icon size={11} strokeWidth={1.9} className="shrink-0" />
                <span className="truncate">{tab.label}</span>
              </button>
              <button
                type="button"
                onClick={e => {
                  e.stopPropagation()
                  onCloseTab(tab.id)
                }}
                className="mr-1 rounded-sm p-[1px] text-[var(--muted-foreground)] opacity-60 transition-opacity hover:bg-[var(--muted)]/70 hover:text-[var(--foreground)] hover:opacity-100"
                aria-label={t('Close tab')}
              >
                <X size={10} />
              </button>
            </div>
          )
        })}
      </div>
      <button
        type="button"
        onClick={onClosePanel}
        className="mb-1 shrink-0 rounded-md p-1.5 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/45 hover:text-[var(--foreground)]"
        aria-label={t('Close viewer')}
        title={t('Close viewer')}
      >
        <X size={14} />
      </button>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Activity home — the panel's default view (no tab focused)          */
/* ------------------------------------------------------------------ */

/**
 * The merged Activity landing: the session-activity sections (tools, KBs,
 * Space refs, attachments — see ``ActivityBody``) plus a compact opener for
 * a URL or a local file. Clicking an attachment opens it as a file tab in
 * this same panel.
 */
function ActivityHome({
  activity,
  open,
  onOpenAttachment,
  onOpenWebTab,
  onOpenLocalFile,
  onTraceToolOutput,
}: {
  activity: SessionActivity
  open: boolean
  onOpenAttachment: (a: MessageAttachment) => void
  onOpenWebTab: (url: string) => void
  onOpenLocalFile: (file: File) => void
  onTraceToolOutput?: (options: ToolOutputTraceTarget) => void
}) {
  return (
    <div className="h-full overflow-y-auto px-3 py-3">
      <ActivityBody
        activity={activity}
        open={open}
        onOpenAttachment={onOpenAttachment}
        onTraceToolOutput={onTraceToolOutput}
      />
      <ActivityDock
        activity={activity}
        onOpenWebTab={onOpenWebTab}
        onOpenLocalFile={onOpenLocalFile}
        onTraceToolOutput={onTraceToolOutput}
      />
    </div>
  )
}

/** Dock tab ids: graph / files mirror the data-driven tabs; open is static. */
type ActivityDockTab = 'graph' | 'files' | 'open'

/**
 * Bottom dock of the Activity home: a small tab strip switching between the
 * session's retrieval subgraphs, the sources its tool calls named, and the
 * URL / local-file opener. The initial tab follows the data (graph first) but
 * the user's explicit pick wins for the rest of the session.
 */
function ActivityDock({
  activity,
  onOpenWebTab,
  onOpenLocalFile,
  onTraceToolOutput,
}: {
  activity: SessionActivity
  onOpenWebTab: (url: string) => void
  onOpenLocalFile: (file: File) => void
  onTraceToolOutput?: (options: ToolOutputTraceTarget) => void
}) {
  const { t } = useTranslation()
  const graphs = useMemo(() => collectSessionGraphs(activity), [activity])
  const files = useMemo(() => collectSessionSources(activity), [activity])
  // null = "follow the data"; once the user picks a tab it sticks.
  const [picked, setPicked] = useState<ActivityDockTab | null>(null)
  const [graphIdx, setGraphIdx] = useState(0)
  const [kbFiles, setKbFiles] = useState<KnowledgeBaseFile[]>([])
  const tab: ActivityDockTab =
    picked ?? (graphs.length > 0 ? 'graph' : files.length > 0 ? 'files' : 'open')
  const safeGraphIdx = Math.min(graphIdx, Math.max(graphs.length - 1, 0))
  const activeGraph = graphs[safeGraphIdx]

  useEffect(() => {
    if (tab !== 'files') return
    const kbNames = [
      ...new Set(files.map(file => file.kbName).filter((name): name is string => Boolean(name))),
    ]
    if (kbNames.length === 0) {
      queueMicrotask(() => setKbFiles([]))
      return
    }
    let cancelled = false
    Promise.all(
      kbNames.map(name => listKnowledgeBaseFiles(name).catch(() => [] as KnowledgeBaseFile[]))
    ).then(lists => {
      if (cancelled) return
      setKbFiles(lists.flat().filter(file => file.type !== 'folder'))
    })
    return () => {
      cancelled = true
    }
  }, [files, tab])

  const findKbFile = useCallback(
    (entry: SessionSourceEntry) => {
      if (!entry.filename) return null
      const exact = kbFiles.find(file => file.name === entry.filename)
      if (exact) return exact
      const basename = entry.filename.split('/').pop()
      return (
        kbFiles.find(file => file.name === entry.filename) ??
        (basename ? kbFiles.find(file => file.name.split('/').pop() === basename) : undefined) ??
        null
      )
    },
    [kbFiles]
  )

  const dockTabs: Array<{
    id: ActivityDockTab
    label: string
    icon: typeof Globe
    badge?: number
  }> = [
    {
      id: 'graph',
      label: t('Graph'),
      icon: Share2,
      badge: graphs.length || undefined,
    },
    {
      id: 'files',
      label: t('Files'),
      icon: FileText,
      badge: files.length || undefined,
    },
    { id: 'open', label: t('Open'), icon: FileUp },
  ]

  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-[var(--border)]/55 bg-[var(--card)] shadow-[0_1px_2px_color-mix(in_srgb,var(--foreground)_5%,transparent)]">
      <div
        role="tablist"
        className="flex items-center gap-1 border-b border-[var(--border)]/40 bg-[color-mix(in_srgb,var(--muted)_25%,var(--card))] px-1.5 py-1"
      >
        {dockTabs.map(item => {
          const active = tab === item.id
          const Icon = item.icon
          return (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setPicked(item.id)}
              className={`inline-flex items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] font-medium transition-colors ${
                active
                  ? 'bg-[var(--card)] text-[var(--foreground)] shadow-[0_1px_2px_color-mix(in_srgb,var(--foreground)_8%,transparent)]'
                  : 'text-[var(--muted-foreground)] hover:text-[var(--foreground)]'
              }`}
            >
              <Icon size={12} strokeWidth={1.9} className="shrink-0" />
              <span>{item.label}</span>
              {item.badge ? (
                <span className="rounded-full bg-[var(--muted)]/70 px-1.5 text-[9.5px] font-semibold tabular-nums text-[var(--muted-foreground)]">
                  {item.badge}
                </span>
              ) : null}
            </button>
          )
        })}
      </div>
      {tab === 'graph' ? (
        activeGraph ? (
          <div>
            {graphs.length > 1 ? (
              <div className="flex flex-wrap gap-1 border-b border-[var(--border)]/35 px-2 pt-2 pb-2">
                {graphs.map((entry, idx) => (
                  <button
                    key={`${entry.kbName}:${entry.label}:${idx}`}
                    type="button"
                    onClick={() => setGraphIdx(idx)}
                    title={entry.label}
                    className={`max-w-[180px] truncate rounded-full border px-2 py-[3px] text-[10px] font-medium transition-colors ${
                      idx === safeGraphIdx
                        ? 'border-[var(--primary)]/45 bg-[var(--primary)]/10 text-[var(--primary)]'
                        : 'border-[var(--border)]/55 text-[var(--muted-foreground)] hover:border-[var(--primary)]/30 hover:text-[var(--foreground)]'
                    }`}
                  >
                    {entry.label}
                  </button>
                ))}
              </div>
            ) : null}
            <GraphSubgraphView
              key={`${safeGraphIdx}:${activeGraph.label}`}
              graph={activeGraph.graph}
              onTrace={
                activeGraph.callId || activeGraph.messageIndex !== undefined
                  ? () =>
                      onTraceToolOutput?.({
                        callId: activeGraph.callId,
                        messageIndex: activeGraph.messageIndex,
                        query: activeGraph.query || activeGraph.label,
                        terms: buildToolTraceTerms({
                          query: activeGraph.query || activeGraph.label,
                          graph: activeGraph.graph,
                        }),
                      })
                  : undefined
              }
            />
          </div>
        ) : (
          <DockEmpty text={t('No graph retrievals in this session yet.')} />
        )
      ) : tab === 'files' ? (
        files.length > 0 ? (
          <ul className="max-h-[280px] overflow-y-auto px-2 py-2">
            {files.map(file => {
              const kbFile = findKbFile(file)
              const href =
                file.kbName && (kbFile?.name || file.filename)
                  ? knowledgeBaseFileRoute(file.kbName, kbFile?.name || file.filename)
                  : file.url
              return (
                <li key={`${file.label}:${file.callId ?? ''}`} title={file.label}>
                  <div className="flex items-center gap-2 rounded-md px-1.5 py-1 text-[11.5px] text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/35">
                    <FileText
                      size={12}
                      strokeWidth={1.8}
                      className="shrink-0 text-[var(--muted-foreground)]"
                    />
                    {href ? (
                      <a
                        href={href}
                        target="_blank"
                        rel="noreferrer"
                        className="min-w-0 flex-1 truncate transition-colors hover:text-[var(--primary)]"
                      >
                        {kbFile?.name || file.filename || file.label}
                      </a>
                    ) : (
                      <span className="min-w-0 flex-1 truncate">
                        {kbFile?.name || file.filename || file.label}
                      </span>
                    )}
                    <span className="shrink-0 rounded-full bg-[var(--muted)]/55 px-1.5 py-[1px] text-[9.5px] font-semibold tabular-nums text-[var(--muted-foreground)]">
                      ×{file.count}
                    </span>
                    {file.callId || file.messageIndex !== undefined ? (
                      <button
                        type="button"
                        onClick={() =>
                          onTraceToolOutput?.({
                            callId: file.callId,
                            messageIndex: file.messageIndex,
                            query: file.query,
                            terms: buildToolTraceTerms({
                              query: file.query,
                              sources: [
                                {
                                  filename: file.filename,
                                  title: file.label,
                                  source: file.filename,
                                  kb_name: file.kbName,
                                },
                              ],
                            }),
                          })
                        }
                        title={t('Locate related answer')}
                        aria-label={t('Locate related answer')}
                        className="shrink-0 rounded-md p-1 text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/70 hover:text-[var(--primary)]"
                      >
                        <LocateFixed size={12} strokeWidth={1.9} />
                      </button>
                    ) : null}
                  </div>
                </li>
              )
            })}
          </ul>
        ) : (
          <DockEmpty text={t('No retrieved sources in this session yet.')} />
        )
      ) : (
        <ActivityOpener onOpenWebTab={onOpenWebTab} onOpenLocalFile={onOpenLocalFile} />
      )}
    </div>
  )
}

function DockEmpty({ text }: { text: string }) {
  return (
    <div className="px-4 py-8 text-center text-[11.5px] text-[var(--muted-foreground)]/75">
      {text}
    </div>
  )
}

/** Compact "open a URL or local file" footer inside the dock's open tab. */
function ActivityOpener({
  onOpenWebTab,
  onOpenLocalFile,
}: {
  onOpenWebTab: (url: string) => void
  onOpenLocalFile: (file: File) => void
}) {
  const { t } = useTranslation()
  const [urlInput, setUrlInput] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const submitUrl = useCallback(() => {
    const trimmed = urlInput.trim()
    if (!trimmed) return
    const href = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`
    onOpenWebTab(href)
    setUrlInput('')
  }, [urlInput, onOpenWebTab])

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) onOpenLocalFile(file)
      e.target.value = ''
    },
    [onOpenLocalFile]
  )

  return (
    <div className="space-y-2 px-2.5 py-2.5">
      <form
        onSubmit={e => {
          e.preventDefault()
          submitUrl()
        }}
        className="flex items-center gap-2 rounded-lg border border-[var(--border)]/55 bg-[var(--background)] px-2.5 py-1.5 transition-colors focus-within:border-[var(--primary)]/40"
      >
        <Globe size={13} strokeWidth={1.8} className="shrink-0 text-[var(--muted-foreground)]" />
        <input
          type="text"
          value={urlInput}
          onChange={e => setUrlInput(e.target.value)}
          placeholder={t('https://example.com')}
          className="min-w-0 flex-1 bg-transparent text-[12.5px] text-[var(--foreground)] outline-none placeholder:text-[var(--muted-foreground)]/60"
        />
        <button
          type="submit"
          disabled={!urlInput.trim()}
          className="inline-flex shrink-0 items-center gap-1 rounded-md bg-[var(--primary)] px-2 py-1 text-[11px] font-medium text-[var(--primary-foreground)] transition-opacity disabled:opacity-30"
          aria-label={t('Open URL')}
        >
          <ArrowRight size={11} strokeWidth={2.2} />
        </button>
      </form>
      <button
        type="button"
        onClick={() => fileInputRef.current?.click()}
        className="inline-flex w-full items-center justify-center gap-2 rounded-lg border border-[var(--border)]/55 bg-[var(--background)] px-3 py-1.5 text-[12px] font-medium text-[var(--muted-foreground)] transition-colors hover:border-[var(--primary)]/35 hover:text-[var(--primary)]"
      >
        <FileUp size={13} strokeWidth={1.8} />
        {t('Open a local file')}
      </button>
      <input
        ref={fileInputRef}
        type="file"
        className="hidden"
        onChange={handleFileSelect}
        aria-hidden="true"
        tabIndex={-1}
      />
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  File tab body                                                      */
/* ------------------------------------------------------------------ */

function FileTabBody({ source }: { source: FilePreviewSource }) {
  const { t } = useTranslation()
  const previewUrl = useMemo(() => resolveSourceUrl(source, apiUrl), [source])
  const kind = previewKindFor(source)
  const filename = source.filename || t('Attachment')

  // Prefer the served URL; fall back to a data URL for pending (un-sent)
  // base64 attachments so download / open-in-browser still work.
  const fileUrl = useMemo(() => {
    if (previewUrl) return previewUrl
    if (source.base64) {
      const mime = source.mimeType || 'application/octet-stream'
      return `data:${mime};base64,${source.base64}`
    }
    return null
  }, [previewUrl, source.base64, source.mimeType])

  const openInBrowser = useCallback(() => {
    if (fileUrl) window.open(fileUrl, '_blank', 'noopener,noreferrer')
  }, [fileUrl])

  return (
    <div className="flex h-full flex-col">
      {/* The tab already carries the filename + type, so this strip is just
          a minimal action rail — no duplicated name/icon/label. */}
      <div className="flex shrink-0 items-center justify-end gap-0.5 border-b border-[var(--border)]/40 bg-[var(--card)] px-2 py-1">
        {fileUrl ? (
          <a
            href={fileUrl}
            download={filename}
            className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/45 hover:text-[var(--foreground)]"
            title={t('Download')}
          >
            <Download size={13} strokeWidth={1.8} />
          </a>
        ) : null}
        <button
          type="button"
          onClick={openInBrowser}
          disabled={!fileUrl}
          className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium text-[var(--muted-foreground)] transition-colors hover:bg-[var(--muted)]/45 hover:text-[var(--foreground)] disabled:opacity-40"
        >
          <ExternalLink size={11} strokeWidth={1.8} />
          {t('Open in browser')}
        </button>
      </div>
      <div className="relative flex-1 overflow-hidden">
        <PreviewBody source={source} previewUrl={previewUrl} kind={kind} />
      </div>
    </div>
  )
}

const PreviewBody = memo(function PreviewBody({
  source,
  previewUrl,
  kind,
}: {
  source: FilePreviewSource
  previewUrl: string | null
  kind: ReturnType<typeof previewKindFor> | null
}) {
  const filename = source.filename

  if (kind === 'office-text') {
    return (
      <OfficeTextPreview
        filename={filename}
        extractedText={source.extractedText}
        url={previewUrl}
      />
    )
  }

  if (!previewUrl) {
    return <FallbackPreview filename={filename} url={null} reason="legacy" />
  }

  switch (kind) {
    case 'pdf':
      return <PdfPreview url={previewUrl} filename={filename} />
    case 'docx':
      return <DocxPreview url={previewUrl} />
    case 'xlsx':
      return <XlsxPreview url={previewUrl} />
    case 'image':
      return <ImagePreview url={previewUrl} filename={filename} />
    case 'svg':
      return <SvgPreview url={previewUrl} filename={filename} />
    case 'markdown':
      return (
        <div className="h-full overflow-y-auto">
          <MarkdownPreview url={previewUrl} />
        </div>
      )
    case 'code':
    case 'text':
      return (
        <div className="h-full overflow-y-auto">
          <TextPreview url={previewUrl} filename={filename} />
        </div>
      )
    case 'fallback':
    default:
      return <FallbackPreview filename={filename} url={previewUrl} />
  }
})

/* ------------------------------------------------------------------ */
/*  Web tab body — iframe with safety fallback                         */
/* ------------------------------------------------------------------ */

/**
 * Web preview tab. Many sites set `X-Frame-Options: DENY` or a CSP
 * `frame-ancestors` directive that flat-out refuses iframe embedding — a
 * browser-enforced anti-clickjacking measure we can't bypass from the
 * frontend. We can't *detect* the failure reliably either (cross-origin
 * iframes are opaque to JS), so we lean on UX honesty:
 *
 *  • A persistent info banner at the top tells the user upfront that some
 *    sites won't load, and exposes "Open in browser" as a big primary
 *    action right next to it.
 *  • A loading spinner is overlaid until either `onLoad` fires or a soft
 *    timeout (4.5 s) elapses. After the timeout we switch the banner copy
 *    to a more explicit "site likely refused embedding" warning so the
 *    user knows the spinner isn't a real load-in-progress.
 */
function WebTabBody({ url }: { url: string }) {
  const { t } = useTranslation()
  const [loaded, setLoaded] = useState(false)
  const [timedOut, setTimedOut] = useState(false)
  const host = hostnameFor(url)

  const openInBrowser = useCallback(() => {
    window.open(url, '_blank', 'noopener,noreferrer')
  }, [url])

  useEffect(() => {
    const timer = window.setTimeout(() => setTimedOut(true), 4500)
    return () => window.clearTimeout(timer)
  }, [])

  const blocked = timedOut && !loaded

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-[var(--border)]/40 bg-[var(--card)] px-4 py-2.5">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--muted)]/55">
          <Globe size={14} strokeWidth={1.7} className="text-[var(--muted-foreground)]" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12.5px] font-semibold text-[var(--foreground)]">
            {host}
          </div>
          <div className="truncate text-[10px] text-[var(--muted-foreground)]">{url}</div>
        </div>
        <button
          type="button"
          onClick={openInBrowser}
          className={`inline-flex shrink-0 items-center gap-1 rounded-md px-2.5 py-1 text-[11px] font-semibold transition-colors ${
            blocked
              ? 'bg-[var(--primary)] text-[var(--primary-foreground)] hover:bg-[var(--primary)]/90'
              : 'border border-[var(--border)]/55 text-[var(--muted-foreground)] hover:border-[var(--primary)]/35 hover:text-[var(--primary)]'
          }`}
        >
          <ExternalLink size={11} strokeWidth={1.9} />
          {t('Open in browser')}
        </button>
      </div>

      {/* Persistent info banner — explains the iframe limitation. Swaps to
          a louder warning once we suspect the site has refused to embed. */}
      <div
        className={`flex shrink-0 items-start gap-2 border-b border-[var(--border)]/30 px-4 py-2 text-[11px] leading-snug ${
          blocked
            ? 'bg-[color-mix(in_srgb,var(--primary)_8%,var(--card))] text-[var(--foreground)]'
            : 'bg-[color-mix(in_srgb,var(--muted)_45%,var(--card))] text-[var(--muted-foreground)]'
        }`}
      >
        <AlertCircle
          size={12}
          strokeWidth={1.9}
          className={`mt-[1px] shrink-0 ${
            blocked ? 'text-[var(--primary)]' : 'text-[var(--muted-foreground)]'
          }`}
        />
        <span>
          {blocked
            ? t(
                'This site looks like it refused to embed (its security headers block iframes). Use “Open in browser” to view it in a real tab.'
              )
            : t(
                'Many sites refuse to embed for security reasons. If the page below stays blank, use “Open in browser”.'
              )}
        </span>
      </div>

      <div className="relative flex-1 overflow-hidden bg-[var(--background)]">
        <iframe
          key={url}
          src={url}
          title={host}
          onLoad={() => setLoaded(true)}
          className="h-full w-full border-0"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          referrerPolicy="no-referrer"
        />
        {!loaded && !timedOut ? (
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-2 bg-[var(--card)]/70 text-[12px] text-[var(--muted-foreground)] backdrop-blur-sm">
            <Loader2
              size={18}
              strokeWidth={1.7}
              className="animate-spin text-[var(--primary)]/80"
            />
            <span>{t('Loading {{host}}…', { host })}</span>
          </div>
        ) : null}
      </div>
    </div>
  )
}

