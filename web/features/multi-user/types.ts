export type GrantPayload = {
  version: number;
  user_id: string;
  models: {
    llm: Array<Record<string, unknown>>;
  };
  /** null = default (all MCP tools), [] = none, array = whitelist. */
  mcp_tools: string[] | null;
  /** null = follow deployment exec policy, false = always disabled. */
  exec_enabled: boolean | null;
  /**
   * May this user start a local agent process (CLI/ACP backend)? Unlike the
   * other tri-states this one is opt-in: `true` allows, `null`/`false` deny.
   */
  agent_loop_cli: boolean | null;
};

export type ToolOption = { name: string; description?: string };

export type McpToolOption = {
  name: string;
  /** Provider grouping key; `server` is its pre-provider spelling. */
  provider_id?: string;
  server?: string;
  /** `"mcp"` today, `"cli"` once CLI-app providers land. */
  kind?: string;
  description?: string;
};

export type MultiUserResources = {
  models: {
    llm: Array<{
      profile_id: string;
      name: string;
      models?: Array<{ model_id: string; name: string; model?: string }>;
    }>;
  };
  tools: ToolOption[];
  mcp_tools: McpToolOption[];
};
