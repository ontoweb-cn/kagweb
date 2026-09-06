/**
 * Session activity — folding a conversation's messages into what it touched.
 *
 * Pure data reduction over the message list: which tools ran, which knowledge
 * bases and Space objects were referenced, which files the user attached, and
 * which files the assistant produced. No React and no rendering, so the
 * `SessionActivityPanel` that draws it stays presentational and this fold is
 * directly testable.
 */

import type {
  MessageAttachment,
  MessageItem,
  MessageRequestSnapshot,
} from '@/features/chat/ChatStateAdapter'
import type { StreamEvent } from '@/features/chat/model/protocol'

/** Artifact URLs are `/files/outputs/<path under the data root>`. */
const OUTPUTS_URL_PREFIX = '/files/outputs/'

/**
 * Where a generated file sits under the data root, for the row's hover title.
 * Derived from the URL rather than persisted separately, so the backend never
 * ships an absolute server path to the browser. Returns `null` for anything
 * that is not an outputs URL.
 */
export function artifactDiskPath(url?: string): string | null {
  if (!url || !url.startsWith(OUTPUTS_URL_PREFIX)) return null
  const relative = url.slice(OUTPUTS_URL_PREFIX.length)
  try {
    return decodeURIComponent(relative) || null
  } catch {
    // A malformed escape sequence: the raw form is still a usable hint.
    return relative || null
  }
}

/** A retrieved knowledge-graph subgraph (GraphRAG local/drift search). */
export interface ToolGraphSubgraph {
  nodes: Array<{
    id: string
    label: string
    type: string
    description: string
    degree: number
  }>
  edges: Array<{
    source: string
    target: string
    description: string
    weight: number
  }>
}

/** One paired tool_call + tool_result exchange, trimmed for panel display. */
export interface ToolCallDetail {
  /** Search/query argument when the tool took one (rag, web_search, …). */
  query: string
  /** Knowledge base the call targeted, when reported. */
  kbName: string
  /** The tool's returned text, clipped to keep the panel light. */
  result: string
  /** Source records reported alongside the result. */
  sources: Array<Record<string, unknown>>
  /** Knowledge-graph subgraph the retrieval hit (GraphRAG, when present). */
  graph?: ToolGraphSubgraph
  /** Retrieval engine behind the tool (`graphrag` → GraphRAG row naming). */
  provider?: string
  /** Stable trace id shared by the paired tool_call/tool_result events. */
  callId?: string
  /** Original assistant-message index containing this tool exchange. */
  messageIndex?: number
}

export interface ToolUsage {
  name: string
  /** Engine the calls rode on, when the backend reported one (graphrag, …). */
  provider?: string
  count: number
  /** Per-call details (query + returned content + sources), most recent last. */
  calls?: ToolCallDetail[]
}

export interface AttachmentWithOrigin {
  messageIndex: number
  attachment: MessageAttachment
}

export interface SpaceReferenceSummary {
  historySessionIds: string[]
  bookPageCount: number
  bookIds: string[]
  bookPages: Map<string, string[]>
  notebookRecordCount: number
  notebookIds: string[]
  questionEntryIds: number[]
  personas: string[]
  memoryKinds: Array<'summary' | 'profile'>
}

export interface SessionActivity {
  tools: ToolUsage[]
  knowledgeBases: string[]
  space: SpaceReferenceSummary
  /** Files the user uploaded. */
  attachments: AttachmentWithOrigin[]
  /** Files the assistant produced (exec/code_execution/media artifacts).
   *  Split out from uploads so a session's output is one collected list
   *  instead of something you scroll the transcript to find again. */
  artifacts: AttachmentWithOrigin[]
  isEmpty: boolean
}

/**
 * Brand spellings for retrieval engines, matching the trace's label table
 * (`TracePresentation.tsx`); titleCase covers the rest. Kept here so the
 * activity panel can name a `rag` row after the engine it actually used —
 * otherwise the left trace says "GraphRAG 检索" while this panel says "rag".
 */
const PROVIDER_LABELS: Record<string, string> = {
  lightrag: 'LightRAG',
  raganything: 'RAGAnything',
  pageindex: 'PageIndex',
  llamaindex: 'LlamaIndex',
  graphrag: 'GraphRAG',
  duckduckgo: 'DuckDuckGo',
  ddg: 'DuckDuckGo',
  openai: 'OpenAI',
  aliyun_iqs: 'Aliyun IQS',
  iqs: 'IQS',
  bocha: 'Bocha',
  zhipu: 'Zhipu',
}

function providerLabel(slug: string): string {
  const known = PROVIDER_LABELS[slug.toLowerCase()]
  if (known) return known
  return slug
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map(part => part[0].toUpperCase() + part.slice(1))
    .join(' ')
}

/** Row name for a tool-usage entry: engine-branded when one was reported. */
export function toolDisplayName(tool: Pick<ToolUsage, 'name' | 'provider'>) {
  if (tool.provider) return providerLabel(tool.provider)
  if (tool.name === 'rag' || tool.name === 'rag_search') return 'RAG'
  return tool.name
}

/** One retrieval subgraph with its call context, for the dock's graph tab. */
export interface SessionGraphEntry {
  /** Chip label: the query when known, else the tool's display name. */
  label: string
  kbName: string
  provider?: string
  graph: ToolGraphSubgraph
  query?: string
  callId?: string
  messageIndex?: number
}

/**
 * Every GraphRAG subgraph surfaced this session, most recent first. The
 * activity dock's graph tab lists one chip per entry and renders the selected
 * subgraph beneath.
 */
export function collectSessionGraphs(activity: SessionActivity): SessionGraphEntry[] {
  const out: SessionGraphEntry[] = []
  for (const tool of activity.tools) {
    for (const call of tool.calls ?? []) {
      if (call.graph) {
        out.push({
          label: call.query || call.kbName || toolDisplayName(tool),
          kbName: call.kbName,
          provider: call.provider ?? tool.provider,
          graph: call.graph,
          query: call.query,
          callId: call.callId,
          messageIndex: call.messageIndex,
        })
      }
    }
  }
  return out.reverse()
}

/** One distinct retrieval source, deduped across the session's calls. */
export interface SessionSourceEntry {
  label: string
  count: number
  filename?: string
  kbName?: string
  url?: string
  query?: string
  callId?: string
  messageIndex?: number
}

/**
 * Sources named by this session's tool results (titles, filenames, URLs —
 * whatever the backend reported), deduped with hit counts. Feeds the dock's
 * files tab.
 */
export function collectSessionSources(activity: SessionActivity): SessionSourceEntry[] {
  const entries = new Map<string, SessionSourceEntry>()
  for (const tool of activity.tools) {
    for (const call of tool.calls ?? []) {
      for (const source of call.sources) {
        const rawFilename = String(source.filename || source.source || '').trim()
        const url = String(source.url || '').trim()
        const filename =
          rawFilename ||
          (() => {
            if (!url) return ''
            try {
              return decodeURIComponent(
                new URL(url, window.location.href).pathname.split('/').pop() || ''
              )
            } catch {
              return url.split(/[?#]/)[0].split('/').pop() || ''
            }
          })()
        const label = String(
          source.title || source.filename || source.url || source.query || source.type || ''
        ).trim()
        if (!label || label === 'source') continue
        const key = [rawFilename, url, label].filter(Boolean).join('\n')
        const existing = entries.get(key)
        if (existing) {
          existing.count += 1
          continue
        }
        entries.set(key, {
          label,
          count: 1,
          filename,
          kbName: String(source.kb_name || source.kbName || '').trim() || undefined,
          url: url || undefined,
          query: call.query || undefined,
          callId: call.callId,
          messageIndex: call.messageIndex,
        })
      }
    }
  }
  return Array.from(entries.values()).sort(
    (a, b) => b.count - a.count || a.label.localeCompare(b.label)
  )
}

/** Enough context for the transcript to find the answer text a tool supported. */
export interface ToolOutputTraceTarget {
  callId?: string
  messageIndex?: number
  query?: string
  /** Entity names, source names, and query fragments matched against the answer. */
  terms?: string[]
}

/** Add a human-meaningful term for answer-text matching. */
function addTraceTerm(terms: string[], value: unknown): void {
  const term = String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (term.length >= 2 && term.length <= 120 && !terms.includes(term)) terms.push(term)
}

/**
 * Build match terms for locating the part of an assistant answer that used a
 * retrieval. Graph labels and source filenames are much better anchors than a
 * raw call id, while the query captures phrasing the answer may echo.
 */
export function buildToolTraceTerms(input: {
  query?: string
  graph?: ToolGraphSubgraph
  sources?: Array<Record<string, unknown>>
}): string[] {
  const terms: string[] = []
  addTraceTerm(terms, input.query)
  input.graph?.nodes.slice(0, 24).forEach(node => addTraceTerm(terms, node.label))
  input.graph?.edges.slice(0, 24).forEach(edge => {
    addTraceTerm(terms, edge.source)
    addTraceTerm(terms, edge.target)
  })
  input.sources?.slice(0, 12).forEach(source => {
    addTraceTerm(terms, source.filename || source.source)
    addTraceTerm(terms, source.title)
  })
  return terms.slice(0, 40)
}

export function buildSessionActivity(
  messages: MessageItem[],
  options?: { availableKbNames?: Set<string> }
): SessionActivity {
  const availableKbNames = options?.availableKbNames
  const toolCounts = new Map<string, number>()
  const toolCalls = new Map<string, ToolCallDetail[]>()
  const kbs = new Set<string>()
  const historySessionIds = new Set<string>()
  const bookIds = new Set<string>()
  const bookPages = new Map<string, string[]>()
  let bookPageCount = 0
  const notebookIds = new Set<string>()
  let notebookRecordCount = 0
  const questionEntryIds = new Set<number>()
  const personas = new Set<string>()
  const memoryKinds = new Set<'summary' | 'profile'>()
  const attachments: AttachmentWithOrigin[] = []
  const artifacts: AttachmentWithOrigin[] = []

  // Long tool payloads (exec output, big retrieved contexts) would bloat the
  // folded activity state for every session; the panel only ever shows a
  // bounded preview, so clip at fold time.
  const MAX_RESULT_CHARS = 4000
  const MAX_QUERY_CHARS = 300
  const clipText = (value: string, max: number) => {
    const text = value.trim()
    return text.length > max ? `${text.slice(0, max)}…` : text
  }

  messages.forEach((msg, idx) => {
    type PendingToolCall = {
      name: string
      query: string
      kbName: string
      provider: string
      callId?: string
    }

    const pendingCallsById = new Map<string, PendingToolCall>()
    const pendingCallsByName = new Map<string, PendingToolCall[]>()

    const takePendingCall = (name: string, callId?: string): PendingToolCall | null => {
      if (callId) {
        const byId = pendingCallsById.get(callId)
        if (byId) {
          pendingCallsById.delete(callId)
          const queue = pendingCallsByName.get(byId.name)
          const queueIndex = queue?.indexOf(byId) ?? -1
          if (queue && queueIndex >= 0) queue.splice(queueIndex, 1)
          return byId
        }
      }
      return pendingCallsByName.get(name)?.shift() ?? null
    }

    const putPendingCall = (call: PendingToolCall) => {
      if (call.callId) pendingCallsById.set(call.callId, call)
      const queue = pendingCallsByName.get(call.name) ?? []
      queue.push(call)
      pendingCallsByName.set(call.name, queue)
    }

    msg.events?.forEach((event: StreamEvent) => {
      const meta = (event.metadata ?? {}) as {
        tool?: string
        args?: Record<string, unknown>
        sources?: Array<Record<string, unknown>>
        tool_metadata?: Record<string, unknown> | null
        provider?: string
        call_id?: string
        query?: unknown
      }
      const providerMeta =
        (meta.tool_metadata &&
          typeof meta.tool_metadata === 'object' &&
          typeof meta.tool_metadata.provider === 'string' &&
          meta.tool_metadata.provider.trim()) ||
        (typeof meta.provider === 'string' && meta.provider.trim()) ||
        ''

      if (event.type === 'tool_call') {
        const callId = String(meta.call_id || '').trim() || undefined
        const name = String(meta.tool || '') || event.content?.trim() || 'tool'
        toolCounts.set(name, (toolCounts.get(name) ?? 0) + 1)
        const args = meta.args ?? {}
        const queryArg =
          typeof args.query === 'string'
            ? args.query
            : typeof args.command === 'string'
              ? args.command
              : typeof args.path === 'string'
                ? args.path
                : ''
        putPendingCall({
          name,
          query: clipText(queryArg, MAX_QUERY_CHARS),
          kbName: typeof args.kb_name === 'string' ? args.kb_name : '',
          provider: providerMeta,
          callId,
        })
        return
      }

      if (event.type === 'tool_result') {
        const callId = String(meta.call_id || '').trim() || undefined
        const name = String(meta.tool || '') || 'tool'
        const graphMeta =
          meta.tool_metadata && typeof meta.tool_metadata === 'object'
            ? meta.tool_metadata.graph
            : null
        const pending = takePendingCall(name, callId)
        const resultMetadata =
          meta.tool_metadata && typeof meta.tool_metadata === 'object'
            ? (meta.tool_metadata as Record<string, unknown>)
            : {}
        const queryFromResult =
          typeof meta.query === 'string'
            ? meta.query
            : typeof resultMetadata.query === 'string'
              ? resultMetadata.query
              : ''
        const kbNameFromResult = String(resultMetadata.kb_name || '')
        const call: ToolCallDetail = {
          query: clipText(pending?.query || queryFromResult, MAX_QUERY_CHARS),
          kbName: pending?.kbName || kbNameFromResult,
          result: clipText(event.content ?? '', MAX_RESULT_CHARS),
          sources: Array.isArray(meta.sources) ? meta.sources : [],
          graph: readGraphSubgraph(graphMeta),
          ...(pending?.provider || providerMeta
            ? { provider: pending?.provider || providerMeta }
            : {}),
          ...(callId || pending?.callId ? { callId: callId || pending?.callId } : {}),
          messageIndex: idx,
        }
        const list = toolCalls.get(name) ?? []
        list.push(call)
        toolCalls.set(name, list)
      }
    })

    msg.attachments?.forEach(a => {
      // `generated` marks files the assistant produced this turn; everything
      // else is something the user attached.
      ;(a.generated ? artifacts : attachments).push({
        messageIndex: idx,
        attachment: a,
      })
    })

    const snap: MessageRequestSnapshot | undefined = msg.requestSnapshot
    if (snap) {
      snap.knowledgeBases?.forEach(k => {
        if (!availableKbNames || availableKbNames.has(k)) kbs.add(k)
      })
      snap.historyReferences?.forEach(s => historySessionIds.add(s))
      snap.bookReferences?.forEach(b => {
        bookIds.add(b.book_id)
        bookPageCount += b.page_ids?.length ?? 0
        const existing = bookPages.get(b.book_id) ?? []
        bookPages.set(b.book_id, [...existing, ...(b.page_ids ?? [])])
      })
      snap.notebookReferences?.forEach(n => {
        notebookIds.add(n.notebook_id)
        notebookRecordCount += n.record_ids?.length ?? 0
      })
      snap.questionNotebookReferences?.forEach(q => questionEntryIds.add(q))
      if (snap.persona) personas.add(snap.persona)
      snap.memoryReferences?.forEach(k => memoryKinds.add(k))
    }
  })

  const tools = Array.from(toolCounts.entries())
    .map(([name, count]) => {
      const calls = toolCalls.get(name)
      const provider = calls?.find(call => call.provider)?.provider
      if (calls && calls.length > 0) {
        return provider ? { name, provider, count, calls } : { name, count, calls }
      }
      return { name, count }
    })
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))

  const space: SpaceReferenceSummary = {
    historySessionIds: Array.from(historySessionIds),
    bookPageCount,
    bookIds: Array.from(bookIds),
    bookPages,
    notebookRecordCount,
    notebookIds: Array.from(notebookIds),
    questionEntryIds: Array.from(questionEntryIds),
    personas: Array.from(personas),
    memoryKinds: Array.from(memoryKinds),
  }

  const isEmpty =
    tools.length === 0 &&
    kbs.size === 0 &&
    attachments.length === 0 &&
    artifacts.length === 0 &&
    space.historySessionIds.length === 0 &&
    space.bookIds.length === 0 &&
    space.notebookIds.length === 0 &&
    space.questionEntryIds.length === 0 &&
    space.personas.length === 0 &&
    space.memoryKinds.length === 0

  return {
    tools,
    knowledgeBases: Array.from(kbs),
    space,
    attachments,
    artifacts,
    isEmpty,
  }
}

/**
 * Normalize the graph payload emitted by GraphRAG and reject anything the
 * canvas cannot safely draw. The backend already bounds the payload; these
 * bounds are repeated here because persisted events may come from older runs.
 */
function readGraphSubgraph(value: unknown): ToolGraphSubgraph | undefined {
  if (!value || typeof value !== 'object') return undefined
  const candidate = value as { nodes?: unknown; edges?: unknown }
  if (!Array.isArray(candidate.nodes) || !Array.isArray(candidate.edges)) {
    return undefined
  }

  const nonEmptyString = (input: unknown): string => {
    if (typeof input !== 'string') return ''
    return input.trim()
  }
  const finiteNumber = (input: unknown, fallback: number): number => {
    return typeof input === 'number' && Number.isFinite(input) ? input : fallback
  }

  const nodes: ToolGraphSubgraph['nodes'] = []
  const nodeIds = new Set<string>()
  for (const item of candidate.nodes.slice(0, 60)) {
    if (!item || typeof item !== 'object') continue
    const record = item as Record<string, unknown>
    const id = nonEmptyString(record.id)
    const label = nonEmptyString(record.label) || id
    if (!id || nodeIds.has(id)) continue
    nodeIds.add(id)
    nodes.push({
      id,
      label,
      type: nonEmptyString(record.type),
      description: nonEmptyString(record.description),
      degree: finiteNumber(record.degree, 0),
    })
  }

  const edges: ToolGraphSubgraph['edges'] = []
  const seenEdges = new Set<string>()
  for (const item of candidate.edges.slice(0, 120)) {
    if (!item || typeof item !== 'object') continue
    const record = item as Record<string, unknown>
    const source = nonEmptyString(record.source)
    const target = nonEmptyString(record.target)
    if (!source || !target || source === target) continue
    if (!nodeIds.has(source) || !nodeIds.has(target)) continue
    const key = source < target ? `${source}\u0000${target}` : `${target}\u0000${source}`
    if (seenEdges.has(key)) continue
    seenEdges.add(key)
    edges.push({
      source,
      target,
      description: nonEmptyString(record.description),
      // Keep weights in a narrow renderable range regardless of the source
      // graph's scoring scale.
      weight: Math.min(10, Math.max(0, finiteNumber(record.weight, 1))),
    })
  }

  return nodes.length > 0 && edges.length > 0 ? { nodes, edges } : undefined
}
