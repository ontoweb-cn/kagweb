'use client'

/**
 * SessionActivityPanel — right-side column of *floating cards* recording
 * the conversation's tools, knowledge bases, Space refs, and attachments.
 *
 * Design notes
 * ────────────
 * • The panel itself has **no background** — cards float over the page so
 *   the chat surface still bleeds through. Each card carries its own border
 *   + faint shadow so it reads as a discrete block.
 * • Clicking an attachment row fires `onOpenAttachment(att)` upward; the
 *   parent routes it into the SessionViewerPanel as a new file tab.
 * • Section content is suppressed entirely when empty — no skeleton cards
 *   for tools/KBs/Space/attachments that never showed up in this session.
 */

import { useEffect, useState, type ReactNode } from 'react'
import Link from 'next/link'
import {
  AtSign,
  BookOpen,
  Brain,
  ChevronDown,
  ClipboardList,
  Database,
  ExternalLink,
  History,
  LocateFixed,
  Network,
  NotebookPen,
  Paperclip,
  Sparkles,
  UserRound,
  Wrench,
  type LucideIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { docIconFor, formatBytes, isSvgFilename } from '@/lib/doc-attachments'
import type { MessageAttachment } from '@/features/chat/ChatStateAdapter'
import { listSessions, type SessionSummary } from '@/lib/session-api'
import { listNotebooks, type NotebookSummary } from '@/lib/notebook-api'
import { bookApi } from '@/lib/book-api'
import type { Book } from '@/lib/book-types'
import GraphSubgraphView from './GraphSubgraphView'

import {
  artifactDiskPath,
  buildToolTraceTerms,
  type AttachmentWithOrigin,
  type SessionActivity,
  type SpaceReferenceSummary,
  type ToolCallDetail,
  type ToolOutputTraceTarget,
  toolDisplayName,
  type ToolUsage,
} from '@/lib/session-activity'

// Re-exported so existing importers (page.tsx, SessionViewerPanel) keep reaching
// the panel for its own contract while the fold itself lives in lib/.
export type {
  AttachmentWithOrigin,
  ToolCallDetail,
  SessionActivity,
  SpaceReferenceSummary,
  ToolUsage,
} from '@/lib/session-activity'
export { buildSessionActivity } from '@/lib/session-activity'

/* ------------------------------------------------------------------ */
/*  Title resolver — lazy id -> title for Space items                  */
/* ------------------------------------------------------------------ */

interface ResolvedTitles {
  sessions: Map<string, string>
  notebooks: Map<string, string>
  books: Map<string, string>
}

function useResolvedTitles(activity: SessionActivity, open: boolean): ResolvedTitles {
  const [sessions, setSessions] = useState<Map<string, string>>(new Map())
  const [notebooks, setNotebooks] = useState<Map<string, string>>(new Map())
  const [books, setBooks] = useState<Map<string, string>>(new Map())

  const needsSessions = activity.space.historySessionIds.length > 0
  const needsNotebooks = activity.space.notebookIds.length > 0
  const needsBooks = activity.space.bookIds.length > 0

  useEffect(() => {
    if (!open || !needsSessions || sessions.size > 0) return
    let cancelled = false
    listSessions(200)
      .then((rows: SessionSummary[]) => {
        if (cancelled) return
        const map = new Map<string, string>()
        rows.forEach(r => map.set(r.session_id, r.title || r.session_id))
        setSessions(map)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [open, needsSessions, sessions.size])

  useEffect(() => {
    if (!open || !needsNotebooks || notebooks.size > 0) return
    let cancelled = false
    listNotebooks()
      .then((rows: NotebookSummary[]) => {
        if (cancelled) return
        const map = new Map<string, string>()
        rows.forEach(r => map.set(r.id, r.name || r.id))
        setNotebooks(map)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [open, needsNotebooks, notebooks.size])

  useEffect(() => {
    if (!open || !needsBooks || books.size > 0) return
    let cancelled = false
    bookApi
      .list()
      .then(({ books: rows }: { books: Book[] }) => {
        if (cancelled) return
        const map = new Map<string, string>()
        rows.forEach(r => map.set(r.id, r.title || r.id))
        setBooks(map)
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [open, needsBooks, books.size])

  return { sessions, notebooks, books }
}

/* ------------------------------------------------------------------ */
/*  Activity body                                                      */
/*                                                                     */
/*  Rendered as the "Activity" home view inside SessionViewerPanel (it */
/*  used to live in its own floating-card panel; the two were merged   */
/*  so the session's activity is the viewer's landing and files open   */
/*  as tabs alongside it).                                             */
/* ------------------------------------------------------------------ */

interface SpaceCategoryDef {
  key: string
  href: string
  label: string
  icon: LucideIcon
}

const SPACE_CATEGORIES: Record<string, SpaceCategoryDef> = {
  chat_history: {
    key: 'chat_history',
    href: '/space/chat-history',
    label: 'Chat history',
    icon: History,
  },
  books: {
    key: 'books',
    href: '/books',
    label: 'Books',
    icon: BookOpen,
  },
  notebooks: {
    key: 'notebooks',
    href: '/notebooks',
    label: 'Notebooks',
    icon: NotebookPen,
  },
  question_bank: {
    key: 'question_bank',
    href: '/space/questions',
    label: 'Question bank',
    icon: ClipboardList,
  },
  persona: {
    key: 'persona',
    href: '/space/personas',
    label: 'Persona',
    icon: UserRound,
  },
  memory: {
    key: 'memory',
    href: '/memory',
    label: 'Memory',
    icon: Brain,
  },
}

export function ActivityBody({
  activity,
  open,
  onOpenAttachment,
  configSection,
  onTraceToolOutput,
}: {
  activity: SessionActivity
  open: boolean
  onOpenAttachment: (a: MessageAttachment) => void
  configSection?: ReactNode
  onTraceToolOutput?: (options: ToolOutputTraceTarget) => void
}) {
  const { t } = useTranslation()
  const { tools, knowledgeBases, space, attachments, artifacts } = activity
  const { sessions, notebooks, books } = useResolvedTitles(activity, open)

  const spaceSubsections: ReactNode[] = []
  if (space.historySessionIds.length > 0) {
    spaceSubsections.push(
      <SpaceSubsection
        key="chat_history"
        category={SPACE_CATEGORIES.chat_history}
        count={space.historySessionIds.length}
      >
        {space.historySessionIds.map(id => (
          <SpaceItemRow key={id} title={sessions.get(id) ?? id} subtitle={id.slice(0, 8)} />
        ))}
      </SpaceSubsection>
    )
  }
  if (space.bookIds.length > 0) {
    spaceSubsections.push(
      <SpaceSubsection key="books" category={SPACE_CATEGORIES.books} count={space.bookIds.length}>
        {space.bookIds.map(id => {
          const pages = space.bookPages.get(id)?.length ?? 0
          return (
            <SpaceItemRow
              key={id}
              title={books.get(id) ?? id}
              subtitle={t('{{n}} page(s)', { n: pages })}
            />
          )
        })}
      </SpaceSubsection>
    )
  }
  if (space.notebookIds.length > 0) {
    spaceSubsections.push(
      <SpaceSubsection
        key="notebooks"
        category={SPACE_CATEGORIES.notebooks}
        count={space.notebookIds.length}
      >
        {space.notebookIds.map(id => (
          <SpaceItemRow key={id} title={notebooks.get(id) ?? id} />
        ))}
      </SpaceSubsection>
    )
  }
  if (space.questionEntryIds.length > 0) {
    spaceSubsections.push(
      <SpaceSubsection
        key="question_bank"
        category={SPACE_CATEGORIES.question_bank}
        count={space.questionEntryIds.length}
      >
        {space.questionEntryIds.map(id => (
          <SpaceItemRow key={id} title={t('Question #{{n}}', { n: id })} />
        ))}
      </SpaceSubsection>
    )
  }
  if (space.personas.length > 0) {
    spaceSubsections.push(
      <SpaceSubsection
        key="persona"
        category={SPACE_CATEGORIES.persona}
        count={space.personas.length}
      >
        {space.personas.map(persona => (
          <SpaceItemRow key={persona} title={persona} />
        ))}
      </SpaceSubsection>
    )
  }
  if (space.memoryKinds.length > 0) {
    spaceSubsections.push(
      <SpaceSubsection
        key="memory"
        category={SPACE_CATEGORIES.memory}
        count={space.memoryKinds.length}
      >
        {space.memoryKinds.map(kind => (
          <SpaceItemRow key={kind} title={kind} />
        ))}
      </SpaceSubsection>
    )
  }

  if (activity.isEmpty && !configSection) {
    return (
      <SectionCard icon={Wrench} title={t('Session activity')}>
        <div className="px-3.5 py-5 text-center text-[12px] italic text-[var(--muted-foreground)]/80">
          {t(
            'As you chat, the tools and references you use — and the files the tutor generates — will appear here.'
          )}
        </div>
      </SectionCard>
    )
  }

  return (
    <div className="space-y-2.5">
      {tools.length > 0 ? (
        <SectionCard icon={Wrench} title={t('Tools used')} count={tools.length}>
          <ul className="space-y-0.5 p-1.5">
            {tools.map(tool => (
              <ToolUsageRow key={tool.name} tool={tool} onTraceToolOutput={onTraceToolOutput} />
            ))}
          </ul>
        </SectionCard>
      ) : null}

      {knowledgeBases.length > 0 ? (
        <SectionCard icon={Database} title={t('Knowledge bases')} count={knowledgeBases.length}>
          <ul className="space-y-0.5 p-1.5">
            {knowledgeBases.map(kb => (
              <li
                key={kb}
                className="truncate rounded-md px-2 py-1.5 text-[12px] font-medium text-[var(--foreground)] transition-colors hover:bg-[var(--muted)]/35"
              >
                {kb}
              </li>
            ))}
          </ul>
        </SectionCard>
      ) : null}

      {spaceSubsections.length > 0 ? (
        <SectionCard icon={AtSign} title={t('Space')}>
          <div className="space-y-1.5 p-1.5">{spaceSubsections}</div>
        </SectionCard>
      ) : null}

      {/* Above Attachments: what this conversation produced is what you come
          back for, more often than a file you uploaded and already have. */}
      {artifacts.length > 0 ? (
        <SectionCard icon={Sparkles} title={t('Generated files')} count={artifacts.length}>
          <ul className="space-y-0.5 p-1.5">
            {artifacts.map(({ attachment, messageIndex }, i) => (
              <AttachmentRow
                key={`${attachment.id ?? attachment.filename ?? i}-${messageIndex}`}
                attachment={attachment}
                onOpen={() => onOpenAttachment(attachment)}
              />
            ))}
          </ul>
        </SectionCard>
      ) : null}

      {attachments.length > 0 ? (
        <SectionCard icon={Paperclip} title={t('Attachments')} count={attachments.length}>
          <ul className="space-y-0.5 p-1.5">
            {attachments.map(({ attachment, messageIndex }, i) => (
              <AttachmentRow
                key={`${attachment.id ?? attachment.filename ?? i}-${messageIndex}`}
                attachment={attachment}
                onOpen={() => onOpenAttachment(attachment)}
              />
            ))}
          </ul>
        </SectionCard>
      ) : null}

      {configSection}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/*  Card primitives                                                    */
/* ------------------------------------------------------------------ */

/** Short human label for a source record reported by a tool. */
function sourceLabel(source: Record<string, unknown>): string {
  return String(
    source.title || source.filename || source.url || source.query || source.type || 'source'
  )
}

/** One collapsible tool-call exchange; its payload stays hidden by default. */
function ToolCallDetailBlock({
  call,
  index,
  onTraceToolOutput,
}: {
  call: ToolCallDetail
  index: number
  onTraceToolOutput?: (options: ToolOutputTraceTarget) => void
}) {
  const { t } = useTranslation()
  const traceable = Boolean(call.callId || call.messageIndex !== undefined)
  const [open, setOpen] = useState(false)

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--border)]/55 bg-[var(--background)]">
      <div
        className={`flex items-center gap-1 px-2.5 py-1.5 ${
          open ? 'border-b border-[var(--border)]/40' : ''
        }`}
      >
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-1.5 rounded-md text-left transition-colors hover:bg-[var(--muted)]/35"
        >
          {call.kbName ? (
            <span className="shrink-0 rounded-full bg-[var(--muted)]/55 px-1.5 py-[1px] text-[10px] font-semibold text-[var(--muted-foreground)]">
              {call.kbName}
            </span>
          ) : null}
          <span className="min-w-0 flex-1 truncate font-mono text-[10.5px] text-[var(--muted-foreground)]">
            {call.query || t('Call #{{n}}', { n: index + 1 })}
          </span>
          <span className="flex shrink-0 items-center gap-1">
            {call.graph ? (
              <span className="inline-flex items-center gap-0.5 rounded-full bg-[var(--primary)]/10 px-1.5 py-[1px] text-[9.5px] font-semibold text-[var(--primary)]">
                <Network size={9} strokeWidth={2} />
                {t('Graph')}
              </span>
            ) : null}
            {call.sources.length > 0 ? (
              <span className="rounded-full bg-[var(--muted)]/55 px-1.5 py-[1px] text-[9.5px] font-semibold tabular-nums text-[var(--muted-foreground)]">
                {call.sources.length}
              </span>
            ) : null}
            <ChevronDown
              size={12}
              strokeWidth={2}
              className={`text-[var(--muted-foreground)] transition-transform ${
                open ? 'rotate-180' : ''
              }`}
            />
          </span>
        </button>
        {traceable ? (
          <button
            type="button"
            onClick={() =>
              onTraceToolOutput?.({
                callId: call.callId,
                messageIndex: call.messageIndex,
                query: call.query,
                terms: buildToolTraceTerms({
                  query: call.query,
                  graph: call.graph,
                  sources: call.sources,
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
      {open ? (
        <>
          {call.graph ? <GraphSubgraphView graph={call.graph} /> : null}
          {call.result ? (
            <div className="max-h-48 overflow-y-auto whitespace-pre-wrap break-words px-2.5 py-2 text-[11px] leading-[1.6] text-[var(--muted-foreground)]">
              {call.result}
            </div>
          ) : null}
          {call.sources.length > 0 ? (
            <div className="border-t border-[var(--border)]/40 px-2.5 py-1.5 text-[10.5px] leading-[1.5] text-[var(--muted-foreground)]">
              {t('Sources')}: {call.sources.slice(0, 5).map(sourceLabel).join(' · ')}
            </div>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

/**
 * One tool row. Tools whose calls carried detail (query + result + sources)
 * expand in place to show each exchange; plain count-only rows stay inert.
 */
function ToolUsageRow({
  tool,
  onTraceToolOutput,
}: {
  tool: ToolUsage
  onTraceToolOutput?: (options: ToolOutputTraceTarget) => void
}) {
  const [open, setOpen] = useState(false)
  const calls = tool.calls ?? []
  const expandable = calls.length > 0

  return (
    <li>
      <button
        type="button"
        onClick={expandable ? () => setOpen(!open) : undefined}
        aria-expanded={expandable ? open : undefined}
        className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-[12px] text-[var(--foreground)] transition-colors ${
          expandable ? 'hover:bg-[var(--muted)]/35' : 'cursor-default'
        }`}
      >
        <span className="truncate font-medium">{toolDisplayName(tool)}</span>
        <span className="flex shrink-0 items-center gap-1">
          <span className="rounded-full bg-[var(--muted)]/55 px-1.5 py-[1px] text-[10px] font-semibold text-[var(--muted-foreground)]">
            ×{tool.count}
          </span>
          {expandable ? (
            <ChevronDown
              size={12}
              strokeWidth={2}
              className={`text-[var(--muted-foreground)] transition-transform ${
                open ? 'rotate-180' : ''
              }`}
            />
          ) : null}
        </span>
      </button>
      {expandable && open ? (
        <div className="mt-1 space-y-1.5 pb-1 pl-4">
          {calls.map((call, idx) => (
            <ToolCallDetailBlock
              key={`${tool.name}-call-${idx}`}
              index={idx}
              call={call}
              onTraceToolOutput={onTraceToolOutput}
            />
          ))}
        </div>
      ) : null}
    </li>
  )
}

function SectionCard({
  icon: Icon,
  title,
  count,
  children,
}: {
  icon: LucideIcon
  title: string
  count?: number
  children: ReactNode
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-[var(--border)]/55 bg-[var(--card)] shadow-[0_1px_2px_color-mix(in_srgb,var(--foreground)_5%,transparent),0_4px_14px_color-mix(in_srgb,var(--foreground)_5%,transparent)]">
      <header className="flex items-center gap-2 border-b border-[var(--border)]/35 px-3.5 py-2.5">
        <Icon size={13} strokeWidth={1.8} className="shrink-0 text-[var(--muted-foreground)]" />
        <span className="flex-1 text-[12px] font-semibold tracking-[0.005em] text-[var(--foreground)]">
          {title}
        </span>
        {count !== undefined && count > 0 ? (
          <span className="shrink-0 rounded-full bg-[var(--muted)]/55 px-1.5 py-[1px] text-[10px] font-semibold text-[var(--muted-foreground)]">
            {count}
          </span>
        ) : null}
      </header>
      {children}
    </section>
  )
}

function SpaceSubsection({
  category,
  count,
  children,
}: {
  category: SpaceCategoryDef
  count: number
  children: ReactNode
}) {
  const Icon = category.icon
  return (
    <div>
      <Link
        href={category.href}
        className="group flex items-center gap-2 rounded-md px-2 py-1 transition-colors hover:bg-[var(--muted)]/40"
      >
        <Icon size={12} strokeWidth={1.8} className="shrink-0 text-[var(--muted-foreground)]" />
        <span className="flex-1 truncate text-[10.5px] font-semibold uppercase tracking-[0.06em] text-[var(--muted-foreground)] transition-colors group-hover:text-[var(--primary)]">
          {category.label}
        </span>
        <span className="rounded-full bg-[var(--muted)]/55 px-1.5 py-[1px] text-[10px] font-semibold text-[var(--muted-foreground)]">
          {count}
        </span>
        <ExternalLink
          size={10}
          strokeWidth={2}
          className="shrink-0 text-[var(--muted-foreground)] opacity-0 transition-opacity group-hover:opacity-100"
        />
      </Link>
      <ul className="mt-0.5 space-y-px pl-5">{children}</ul>
    </div>
  )
}

function SpaceItemRow({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <li className="flex items-center gap-2 rounded-md px-2 py-1 text-[12px] transition-colors hover:bg-[var(--muted)]/35">
      <span className="block min-w-0 flex-1 truncate font-medium text-[var(--foreground)]">
        {title}
      </span>
      {subtitle ? (
        <span className="shrink-0 text-[10px] text-[var(--muted-foreground)]">{subtitle}</span>
      ) : null}
    </li>
  )
}

function AttachmentRow({
  attachment,
  onOpen,
}: {
  attachment: MessageAttachment
  onOpen: () => void
}) {
  const filename = attachment.filename || 'untitled'
  const spec = docIconFor(filename)
  const Icon = spec.Icon
  const isImage = attachment.type === 'image' || isSvgFilename(filename)
  // Generated files carry a size; showing it distinguishes a real deliverable
  // from an empty stub without opening it. The hover title answers "where did
  // this land on disk?" — the question the transcript cannot.
  const size = attachment.generated ? formatBytes(attachment.size_bytes ?? -1) : ''
  const detail = [spec.label, size].filter(Boolean).join(' · ')
  const diskPath = attachment.generated ? artifactDiskPath(attachment.url) : null

  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        title={diskPath ?? undefined}
        className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-[var(--muted)]/35"
      >
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--muted)]/55">
          <Icon
            size={13}
            strokeWidth={1.6}
            className={isImage ? 'text-[var(--muted-foreground)]' : spec.tint}
          />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[12px] font-medium text-[var(--foreground)]">
            {filename}
          </span>
          <span className="block truncate text-[10px] uppercase tracking-wide text-[var(--muted-foreground)]">
            {detail}
          </span>
        </span>
      </button>
    </li>
  )
}
