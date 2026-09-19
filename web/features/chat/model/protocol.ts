/**
 * UI-facing views of the generated v2 turn protocol.
 *
 * The wire schema remains the only field-level source of truth. The UI event
 * alias only tightens fields that `parseTurnEvent()` normalizes before a
 * surface sees them, while compatibility commands omit the envelope fields
 * added by `UnifiedTurnClient`.
 */
import type {
  CancelTurnCommand,
  ClientCommand as GeneratedClientCommand,
  LLMSelection as GeneratedLLMSelection,
  RegenerateCommand,
  ResumeTurnCommand,
  ServerEvent as GeneratedServerEvent,
  StartTurnCommand,
  StreamEvent as GeneratedStreamEvent,
  StreamEventType as GeneratedStreamEventType,
  SubmitUserReplyCommand,
  SubscribeSessionCommand,
  SubscribeTurnCommand,
  UnsubscribeCommand,
} from "@/contracts/generated/turn-protocol";

export type ClientCommand = GeneratedClientCommand;
export type ServerEvent = GeneratedServerEvent;
export type StreamEventType = GeneratedStreamEventType;
export type LLMSelection = GeneratedLLMSelection;

/**
 * What the user picked in the composer's model control.
 *
 * Either a conversation-catalog pair (the self-hosted Intellect HTTP family,
 * where the catalog *is* the model configuration) or a backend-native model
 * id — an entry of the agent backend's own vocabulary (an ACP option id or an
 * operator-curated name) sent verbatim and validated by the backend. `null`
 * means "backend default".
 */
export type TurnModelSelection = LLMSelection | { backend_model: string };

export function isBackendModelSelection(
  selection: TurnModelSelection | null | undefined,
): selection is { backend_model: string } {
  return typeof selection === "object" && selection !== null && "backend_model" in selection;
}

export type StreamEvent = Omit<
  GeneratedStreamEvent,
  "content" | "metadata" | "source" | "stage" | "session_id"
> & {
  content: string;
  metadata: Record<string, unknown>;
  source: string;
  stage: string;
  session_id?: string;
};

type UnversionedCommand<T, Kind extends string> = Omit<
  T,
  "protocol_version" | "type"
> & { type: Kind };

export type StartTurnMessage = UnversionedCommand<
  StartTurnCommand,
  "start_turn"
>;
export type SubscribeTurnMessage = UnversionedCommand<
  SubscribeTurnCommand,
  "subscribe_turn"
>;
export type SubscribeSessionMessage = UnversionedCommand<
  SubscribeSessionCommand,
  "subscribe_session"
>;
export type ResumeTurnMessage = UnversionedCommand<
  ResumeTurnCommand,
  "resume_from"
>;
export type UnsubscribeMessage = UnversionedCommand<
  UnsubscribeCommand,
  "unsubscribe"
>;
export type CancelTurnMessage = Omit<
  UnversionedCommand<CancelTurnCommand, "cancel_turn">,
  "command_id"
> & { command_id?: string };
export type RegenerateMessage = UnversionedCommand<
  RegenerateCommand,
  "regenerate"
>;
export type SubmitUserReplyMessage = Omit<
  UnversionedCommand<SubmitUserReplyCommand, "submit_user_reply">,
  "command_id"
> & { command_id?: string };

export type ChatMessage =
  | StartTurnMessage
  | SubscribeTurnMessage
  | SubscribeSessionMessage
  | ResumeTurnMessage
  | UnsubscribeMessage
  | CancelTurnMessage
  | RegenerateMessage
  | SubmitUserReplyMessage;
