export type GrantPayload = {
  version: number;
  user_id: string;
  models: {
    llm: Array<Record<string, unknown>>;
  };
  /** null = follow deployment exec policy, false = always disabled. */
  exec_enabled: boolean | null;
  /**
   * May this user start a local agent process (CLI/ACP backend)? Unlike the
   * other tri-states this one is opt-in: `true` allows, `null`/`false` deny.
   */
  agent_loop_cli: boolean | null;
};

export type MultiUserResources = {
  models: {
    llm: Array<{
      profile_id: string;
      name: string;
      models?: Array<{ model_id: string; name: string; model?: string }>;
    }>;
  };
};
