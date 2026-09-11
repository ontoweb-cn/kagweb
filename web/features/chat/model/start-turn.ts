import type {
  BookReference,
  LLMSelection,
  MemoryReferences,
  OutgoingAttachment,
  ReadingReference,
  ReadingViewport,
  TimedMediaViewport,
} from "@/contracts/generated/turn-protocol";

export interface StartTurnInput {
  content: string;
  capability?: string | null;
  sessionId?: string | null;
  language?: string | null;
  capabilityConfig?: Record<string, unknown>;
  allowedCapabilityConfigKeys?: readonly string[];
  attachments?: OutgoingAttachment[];
  historyReferences?: string[];
  partnerGroupReferences?: Array<Record<string, unknown>>;
  bookReferences?: BookReference[];
  readingReferences?: ReadingReference[];
  memoryReferences?: MemoryReferences;
  llmSelection?: LLMSelection | null;
  workspaceMode?: string | null;
  masteryPathId?: string | null;
  masteryPathLeaseManaged?: boolean;
  readingMaterialId?: string | null;
  readingMaterialRevision?: number | null;
  readingWorkspaceId?: string | null;
  readingViewport?: ReadingViewport | null;
  timedMediaId?: string | null;
  timedMediaViewport?: TimedMediaViewport | null;
  parentMessageId?: number | null;
  courseId?: string | null;
  persistUserMessage?: boolean;
  regenerate?: boolean;
  regeneratedFromMessageId?: number | null;
  supersededTurnId?: string | null;
  followupQuestionContext?: Record<string, unknown> | null;
  selectionTutorContext?: Record<string, unknown> | null;
  subagentConsultBudget?: number | null;
  autoRoute?: boolean | null;
}

export interface LegacySendMessageArguments {
  content: string;
  attachments?: OutgoingAttachment[];
  config?: Record<string, unknown>;
  historyReferences?: string[];
  memoryReferences?: MemoryReferences;
}
