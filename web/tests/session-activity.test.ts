import test from 'node:test'
import assert from 'node:assert/strict'
import {
  artifactDiskPath,
  buildToolTraceTerms,
  buildSessionActivity,
  collectSessionGraphs,
  collectSessionSources,
} from '../lib/session-activity'
import type { MessageItem, MessageRequestSnapshot } from '../features/chat/ChatStateAdapter'
import type { StreamEvent } from '../features/chat/model/protocol'

/** A StreamEvent with only the fields the fold reads set explicitly. */
function event(type: string, metadata: Record<string, unknown> = {}, content = ''): StreamEvent {
  return {
    type: type as StreamEvent['type'],
    source: '',
    stage: '',
    content,
    metadata,
    timestamp: 0,
  }
}

/** A request snapshot with only the fields the fold reads set explicitly. */
function snapshot(fields: Partial<MessageRequestSnapshot> = {}): MessageRequestSnapshot {
  return {
    content: '',
    enabledTools: [],
    knowledgeBases: [],
    language: 'en',
    ...fields,
  }
}

function messages(...items: Partial<MessageItem>[]): MessageItem[] {
  return items.map(
    (item, idx) =>
      ({
        id: idx + 1,
        role: idx % 2 === 0 ? 'user' : 'assistant',
        content: '',
        ...item,
      }) as MessageItem
  )
}

const UPLOAD = {
  type: 'document',
  filename: 'syllabus.pdf',
  id: 'att-1',
  url: '/files/attachments/att-1/syllabus.pdf',
}

const ARTIFACT = {
  type: 'document',
  filename: 'summary.pptx',
  generated: true,
  size_bytes: 24_576,
  url: '/files/outputs/workspace/chat/chat/turn_1/exec/summary.pptx',
}

/* ------------------------------------------------------------------ */
/*  uploads vs generated files                                         */
/* ------------------------------------------------------------------ */

test('splits generated files out of user uploads', () => {
  const activity = buildSessionActivity(
    messages(
      { role: 'user', attachments: [UPLOAD] },
      { role: 'assistant', attachments: [ARTIFACT] }
    )
  )

  assert.equal(activity.attachments.length, 1)
  assert.equal(activity.attachments[0].attachment.filename, 'syllabus.pdf')
  assert.equal(activity.artifacts.length, 1)
  assert.equal(activity.artifacts[0].attachment.filename, 'summary.pptx')
})

test('keeps the originating message index on both lists', () => {
  const activity = buildSessionActivity(
    messages(
      { role: 'user', attachments: [UPLOAD] },
      { role: 'assistant', attachments: [ARTIFACT] }
    )
  )

  assert.equal(activity.attachments[0].messageIndex, 0)
  assert.equal(activity.artifacts[0].messageIndex, 1)
})

test('collects generated files across every turn in order', () => {
  const second = { ...ARTIFACT, filename: 'chart.png', type: 'image' }
  const activity = buildSessionActivity(
    messages(
      { role: 'user' },
      { role: 'assistant', attachments: [ARTIFACT] },
      { role: 'user' },
      { role: 'assistant', attachments: [second] }
    )
  )

  assert.deepEqual(
    activity.artifacts.map(a => a.attachment.filename),
    ['summary.pptx', 'chart.png']
  )
})

test('a session with only generated files is not empty', () => {
  // Regression guard: isEmpty ignoring artifacts would render the empty-state
  // card over a session that did produce something.
  const activity = buildSessionActivity(messages({ role: 'assistant', attachments: [ARTIFACT] }))

  assert.equal(activity.isEmpty, false)
  assert.equal(activity.attachments.length, 0)
})

test('a session with nothing at all is empty', () => {
  const activity = buildSessionActivity(messages({ role: 'user' }))

  assert.equal(activity.isEmpty, true)
  assert.equal(activity.artifacts.length, 0)
})

/* ------------------------------------------------------------------ */
/*  tools / knowledge bases                                            */
/* ------------------------------------------------------------------ */

test('counts tool calls and orders them by frequency', () => {
  const activity = buildSessionActivity(
    messages({
      role: 'assistant',
      events: [
        event('tool_call', { tool: 'rag' }),
        event('tool_call', { tool: 'exec' }),
        event('tool_call', { tool: 'rag' }),
        event('content'),
      ],
    })
  )

  assert.deepEqual(activity.tools, [
    { name: 'rag', count: 2 },
    { name: 'exec', count: 1 },
  ])
})

test('dedupes knowledge bases across turns', () => {
  const activity = buildSessionActivity(
    messages(
      { role: 'user', requestSnapshot: snapshot({ knowledgeBases: ['KB'] }) },
      { role: 'user', requestSnapshot: snapshot({ knowledgeBases: ['KB'] }) }
    )
  )

  assert.deepEqual(activity.knowledgeBases, ['KB'])
})

test('pairs rag tool calls with their retrieved content', () => {
  const activity = buildSessionActivity(
    messages({
      role: 'assistant',
      events: [
        event('tool_call', { tool: 'rag', args: { query: '什么是约束解码', kb_name: 'test' } }),
        event(
          'tool_result',
          {
            tool: 'rag',
            sources: [{ type: 'rag', kb_name: 'test', query: '什么是约束解码' }],
          },
          'Constrained decoding forces every emitted token to keep the'
        ),
      ],
    })
  )

  assert.equal(activity.tools[0].name, 'rag')
  assert.equal(activity.tools[0].count, 1)
  const calls = activity.tools[0].calls ?? []
  assert.equal(calls.length, 1)
  assert.equal(calls[0].query, '什么是约束解码')
  assert.equal(calls[0].kbName, 'test')
  assert.equal(calls[0].result, 'Constrained decoding forces every emitted token to keep the')
  assert.deepEqual(calls[0].sources, [{ type: 'rag', kb_name: 'test', query: '什么是约束解码' }])
})

test('pairs parallel tool calls by call id when results finish out of order', () => {
  const graph = {
    nodes: [
      {
        id: 'a',
        label: 'A',
        type: 'entity',
        description: '',
        degree: 1,
      },
      {
        id: 'b',
        label: 'B',
        type: 'entity',
        description: '',
        degree: 1,
      },
    ],
    edges: [{ source: 'a', target: 'b', description: 'relates to', weight: 1 }],
  }
  const activity = buildSessionActivity(
    messages({
      role: 'assistant',
      events: [
        event('tool_call', {
          tool: 'rag',
          call_id: 'call-1',
          args: { query: '青年突击队', kb_name: '规章制度' },
        }),
        event('tool_call', {
          tool: 'rag',
          call_id: 'call-2',
          args: { query: '约束解码', kb_name: 'test' },
        }),
        event('tool_result', {
          tool: 'rag',
          call_id: 'call-2',
          tool_metadata: { provider: 'graphrag', kb_name: 'test', graph },
        }),
        event('tool_result', {
          tool: 'rag',
          call_id: 'call-1',
          tool_metadata: { provider: 'graphrag', kb_name: '规章制度', graph },
        }),
      ],
    })
  )

  const calls = activity.tools[0].calls ?? []
  assert.deepEqual(
    calls.map(call => call.query),
    ['约束解码', '青年突击队']
  )
  assert.deepEqual(
    collectSessionGraphs(activity).map(entry => entry.label),
    ['青年突击队', '约束解码']
  )
})

test('collects a valid GraphRAG subgraph from tool metadata', () => {
  const graph = {
    nodes: [
      {
        id: 'constrained decoding',
        label: 'Constrained decoding',
        type: 'concept',
        description: 'A decoding strategy.',
        degree: 2,
      },
      { id: 'language model', label: 'Language model', type: '', description: '', degree: 1 },
    ],
    edges: [
      {
        source: 'constrained decoding',
        target: 'language model',
        description: 'Used by',
        weight: 0.8,
      },
    ],
  }
  const activity = buildSessionActivity(
    messages({
      role: 'assistant',
      events: [
        event('tool_call', { tool: 'rag', args: { query: 'graph query' } }),
        event(
          'tool_result',
          {
            tool: 'rag',
            tool_metadata: { graph },
          },
          'GraphRAG result'
        ),
      ],
    })
  )

  assert.deepEqual(activity.tools[0].calls?.[0].graph, graph)
})

test('keeps trace provenance on retrieved files and graphs', () => {
  const graph = {
    nodes: [
      {
        id: 'Meta-Ctrl',
        label: 'Meta-Ctrl',
        type: 'method',
        description: 'A constrained decoding method.',
        degree: 1,
      },
      {
        id: 'DFA',
        label: 'DFA',
        type: '',
        description: '',
        degree: 1,
      },
    ],
    edges: [
      {
        source: 'Meta-Ctrl',
        target: 'DFA',
        description: 'encodes constraints in',
        weight: 1,
      },
    ],
  }
  const activity = buildSessionActivity(
    messages({
      role: 'assistant',
      events: [
        event('tool_call', {
          call_id: 'rag-call-1',
          tool: 'rag',
          args: { query: 'Meta-Ctrl', kb_name: 'test1' },
        }),
        event('tool_result', {
          call_id: 'rag-call-1',
          tool: 'rag',
          tool_metadata: { graph },
          sources: [
            {
              title: 'Meta-Ctrl.pdf',
              source: 'Meta-Ctrl.pdf',
              kb_name: 'test1',
            },
          ],
        }),
      ],
    })
  )
  const files = collectSessionSources(activity)
  const graphs = collectSessionGraphs(activity)

  assert.equal(files.length, 1)
  assert.equal(files[0].filename, 'Meta-Ctrl.pdf')
  assert.equal(files[0].kbName, 'test1')
  assert.equal(files[0].callId, 'rag-call-1')
  assert.equal(files[0].messageIndex, 0)
  assert.equal(files[0].query, 'Meta-Ctrl')
  assert.equal(graphs.length, 1)
  assert.equal(graphs[0].callId, 'rag-call-1')
  assert.equal(graphs[0].messageIndex, 0)
  assert.equal(graphs[0].query, 'Meta-Ctrl')

  const terms = buildToolTraceTerms({
    query: 'DFA 的状态数会不会爆炸？',
    graph,
    sources: [{ filename: 'Meta-Ctrl.pdf', title: 'Meta-Ctrl.pdf' }],
  })
  assert.ok(terms.includes('DFA 的状态数会不会爆炸？'))
  assert.ok(terms.includes('Meta-Ctrl'))
  assert.ok(terms.includes('DFA'))
  assert.ok(terms.includes('Meta-Ctrl.pdf'))
})

test('drops malformed or disconnected graph payloads', () => {
  const activity = buildSessionActivity(
    messages({
      role: 'assistant',
      events: [
        event('tool_call', { tool: 'rag', args: { query: 'invalid graph' } }),
        event(
          'tool_result',
          {
            tool: 'rag',
            tool_metadata: {
              graph: {
                nodes: [{ label: 'No id' }],
                edges: [{ source: 'missing', target: 'also-missing' }],
              },
            },
          },
          'GraphRAG result'
        ),
      ],
    })
  )

  assert.equal(activity.tools[0].calls?.[0].graph, undefined)
})

test('clips oversized tool results at fold time', () => {
  const big = 'x'.repeat(5000)
  const activity = buildSessionActivity(
    messages({
      role: 'assistant',
      events: [
        event('tool_call', { tool: 'rag', args: { query: 'q', kb_name: 'kb' } }),
        event('tool_result', { tool: 'rag' }, big),
      ],
    })
  )

  const result = activity.tools[0].calls?.[0].result ?? ''
  assert.equal(result.length, 4001)
  assert.ok(result.endsWith('…'))
})

test('a tool_call with no result carries a count but no expandable calls', () => {
  const activity = buildSessionActivity(
    messages({
      role: 'assistant',
      events: [event('tool_call', { tool: 'rag', args: { query: 'q' } })],
    })
  )

  assert.deepEqual(activity.tools, [{ name: 'rag', count: 1 }])
})

test('hides deleted knowledge bases when an available set is given', () => {
  const activity = buildSessionActivity(
    messages({
      role: 'user',
      requestSnapshot: snapshot({ knowledgeBases: ['gone', 'alive'] }),
    }),
    { availableKbNames: new Set(['alive']) }
  )

  assert.deepEqual(activity.knowledgeBases, ['alive'])
})

test('hides every stale knowledge base when the confirmed set is empty', () => {
  const activity = buildSessionActivity(
    messages({
      role: 'user',
      requestSnapshot: snapshot({ knowledgeBases: ['last-deleted-kb'] }),
    }),
    { availableKbNames: new Set() }
  )

  assert.deepEqual(activity.knowledgeBases, [])
})

test('keeps knowledge bases when availability could not be loaded', () => {
  const activity = buildSessionActivity(
    messages({
      role: 'user',
      requestSnapshot: snapshot({ knowledgeBases: ['unconfirmed'] }),
    })
  )

  assert.deepEqual(activity.knowledgeBases, ['unconfirmed'])
})

/* ------------------------------------------------------------------ */
/*  artifactDiskPath                                                   */
/* ------------------------------------------------------------------ */

test('derives the data-root-relative path from an outputs URL', () => {
  assert.equal(artifactDiskPath(ARTIFACT.url), 'workspace/chat/chat/turn_1/exec/summary.pptx')
})

test('decodes percent-escaped segments', () => {
  assert.equal(
    artifactDiskPath('/files/outputs/workspace/chat/chat/t/exec/%E6%8A%A5%E5%91%8A.pptx'),
    'workspace/chat/chat/t/exec/报告.pptx'
  )
})

test('falls back to the raw form on a malformed escape', () => {
  assert.equal(artifactDiskPath('/files/outputs/bad%zz.pptx'), 'bad%zz.pptx')
})

test('returns null for uploads and for missing URLs', () => {
  assert.equal(artifactDiskPath(UPLOAD.url), null)
  assert.equal(artifactDiskPath(undefined), null)
  assert.equal(artifactDiskPath(''), null)
})
