import { describe, expect, it } from 'vitest'
import { extractMessageSegments, extractAskUserPayload } from '../components/chat/home/AskUserOptions'
import type { StreamEvent } from '../features/chat/model/protocol'

// The exact event sequence KAGWeb's agent-loop approval flow emits
// (capability.py _handle_approval_request): tool_call + tool_result carrying
// metadata.tool_metadata.ask_user, then the decision progress event.
function approvalEvents(): StreamEvent[] {
  const questions = [
    {
      id: 'approval',
      header: '需要授权',
      prompt: "The agent wants to run 'shell'. Allow it to continue?",
      options: [
        { value: 'once', label: 'Allow once', description: 'Approve this single request' },
        { value: 'session', label: 'Allow for this session', description: 'Approve similar requests' },
        { value: 'always', label: 'Always allow', description: 'Remember this approval' },
        { value: 'deny', label: 'Deny', description: 'Do not run it' },
      ],
      multi_select: false,
    },
  ]
  return [
    {
      type: 'tool_call',
      source: 'chat',
      stage: 'responding',
      content: '',
      metadata: { call_id: 'approval-req-1', call_state: 'running', args: { questions } },
      turn_id: 't1',
      seq: 1,
    },
    {
      type: 'tool_result',
      source: 'chat',
      stage: 'responding',
      content: '',
      metadata: {
        call_id: 'approval-req-1',
        tool_metadata: { ask_user: { questions } },
      },
      turn_id: 't1',
      seq: 2,
    },
  ] as unknown as StreamEvent[]
}

describe('agent-loop approval card rendering', () => {
  it('builds an interactive ask_user segment from the approval events', () => {
    const events = approvalEvents()
    const payload = extractAskUserPayload(events)
    expect(payload).not.toBeNull()
    expect(payload?.resolved).toBe(false)
    expect(payload?.payload.questions[0]?.options).toHaveLength(4)

    const segments = extractMessageSegments(events)
    const card = segments.find(seg => seg.kind === 'ask_user')
    expect(card).toBeDefined()
  })
})
