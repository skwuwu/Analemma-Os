/**
 * ReactExecutor — Bridge-governed ReAct loop using Claude's native tool_use protocol.
 *
 * Wraps every agent tool call through AnalemmaBridge.segment() for governance,
 * then injects tool results back into the conversation for the next LLM turn.
 *
 * Governance model:
 *   - Every tool call passes through bridge.segment() BEFORE handler execution
 *   - Parallel tool calls use atomic governance: if ANY tool in a batch receives
 *     SIGKILL, ALL tools in the batch are aborted (no partial execution)
 *   - Budget checked at 3 points: pre-loop, post-LLM, post-tool
 *
 * Execution flow:
 *   [1] Build messages (user task)
 *   [2] Call LLM via injected LLMClient with tool definitions
 *   [3] Post-LLM budget gate — stop before tool execution if budget blown
 *   [4] Atomic governance: probe bridge.segment() for ALL tools in batch
 *   [5] Kill check: if ANY SIGKILL → abort entire batch
 *   [6] Execute approved handlers, return errors for rejected
 *   [7] Post-tool budget estimate — stop if next LLM call will exceed budget
 *   [8] Loop until end_turn / max_iterations / budget_exceeded / sigkill
 *
 * Race Condition Prevention:
 *   processToolBatch() uses strictly sequential phases:
 *     Phase 1: ALL governance probes complete (Promise.all) — no handlers run
 *     Phase 2: Synchronous kill-check on all decisions
 *     Phase 3: Sequential handler execution — only entered when Phase 2 passes
 *
 * Usage:
 *   const bridge = await AnalemmaBridge.create({ workflowId: "agent_001", ... });
 *   const executor = new ReactExecutor({ bridge, llmClient: new MyLLMClient() });
 *   executor.registerTool("read_file", "Read a file", schema, handler);
 *   const result = await executor.run("Analyze the codebase for issues");
 */

import { AnalemmaBridge, SecurityViolation, SegmentOutcome } from "./bridge";
import { SegmentType, CommitStatus } from "./types";

// ─── Constants ──────────────────────────────────────────────────────────────

const LLM_MAX_RETRIES = 3;
const LLM_BASE_DELAY_MS = 1000;
const MAX_CONSECUTIVE_REJECTIONS = 3;
const TOKEN_ESTIMATE_CHARS_PER_TOKEN = 4;

// ─── LLM Client Abstraction (Strategy Pattern) ─────────────────────────────

/**
 * A single content block in an LLM message.
 * Mirrors the Anthropic Messages API content block types.
 */
export type LLMContentBlock =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> };

/**
 * Token usage from a single LLM call.
 */
export interface LLMUsage {
  input_tokens: number;
  output_tokens: number;
}

/**
 * Response from an LLM call.
 * Provider-agnostic shape that any LLM client adapter must produce.
 */
export interface LLMResponse {
  content: LLMContentBlock[];
  stop_reason: "end_turn" | "tool_use" | "max_tokens" | string;
  usage: LLMUsage;
}

/**
 * A single message in the conversation history.
 */
export interface ConversationMessage {
  role: "user" | "assistant";
  content: string | LLMContentBlock[] | ToolResultBlock[];
}

/**
 * Tool result content block for the Messages API.
 */
export interface ToolResultBlock {
  type: "tool_result";
  tool_use_id: string;
  content: string;
  is_error?: boolean;
}

/**
 * Tool definition in Anthropic Messages API format.
 */
export interface ToolDefinition {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
}

/**
 * Options for LLM createMessage call.
 */
export interface LLMCallOptions {
  system: string;
  max_tokens: number;
  temperature?: number;
  timeout_ms?: number;
}

/**
 * LLM client interface — injected by the consuming application.
 *
 * Implementations:
 *   - AnthropicDirectClient (wraps @anthropic-ai/sdk)
 *   - BedrockClient (wraps @aws-sdk/client-bedrock-runtime)
 *   - Custom (for testing or other providers)
 */
export interface LLMClient {
  createMessage(
    messages: ConversationMessage[],
    tools: ToolDefinition[],
    options: LLMCallOptions,
  ): Promise<LLMResponse>;
}

// ─── Tool Registration ──────────────────────────────────────────────────────

/**
 * A tool handler function. Receives validated params, returns result.
 * Supports both sync and async handlers.
 */
export type ToolHandler = (
  params: Record<string, unknown>,
) => unknown | Promise<unknown>;

/**
 * Internal representation of a registered tool.
 */
export interface RegisteredTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  handler: ToolHandler;
  bridgeAction: string;
}

// ─── React Result ───────────────────────────────────────────────────────────

export type StopReason =
  | "end_turn"
  | "max_iterations"
  | "budget_exceeded"
  | "sigkill"
  | "wall_clock_timeout"
  | "max_rejections";

/**
 * Final result of a ReAct execution.
 */
export interface ReactResult {
  finalAnswer: string;
  messages: ConversationMessage[];
  iterations: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  segments: string[];
  stopReason: StopReason;
}

// ─── Governance Decision (internal) ─────────────────────────────────────────

interface GovernanceDecision {
  allowed: boolean;
  wasKilled: boolean;
  shouldRollback: boolean;
  checkpointId: string | null;
  reason: string | null;
  actionParams: Record<string, unknown>;
}

// ─── Configuration ──────────────────────────────────────────────────────────

export interface ReactExecutorConfig {
  /** AnalemmaBridge instance (externally owned lifecycle). */
  bridge: AnalemmaBridge;
  /** Injected LLM client (no default — consumer must provide). */
  llmClient: LLMClient;
  /** Max ReAct loop iterations. Default: 25. */
  maxIterations?: number;
  /** Max tokens per LLM response. Default: 4096. */
  maxTokensPerTurn?: number;
  /** LLM temperature. Default: 0.0. */
  temperature?: number;
  /** Total token budget (aligns with VSM BudgetWatchdog 500K). Default: 500_000. */
  tokenBudget?: number;
  /** System prompt for the agent. Default: "You are a helpful assistant." */
  systemPrompt?: string;
  /**
   * Token counter for budget estimation.
   *
   * Default: text.length / 4 (rough estimate, ~4 chars per token).
   *
   * RECOMMENDED: Inject a precise counter for production use:
   *   - gpt-tokenizer: `import { encode } from 'gpt-tokenizer'; (t) => encode(t).length`
   *   - tiktoken (JS): `import { encoding_for_model } from 'tiktoken'; ...`
   *   - @anthropic-ai/tokenizer: for Anthropic-specific token counts
   *
   * The default estimate is conservative and sufficient for dev/testing but may
   * cause premature budget exhaustion in production.
   */
  tokenCounter?: (text: string) => number;
  /** Wall-clock timeout in ms. Default: undefined (no limit). */
  wallClockTimeoutMs?: number;
  /** Per-tool execution timeout in ms. Default: 30_000. */
  toolTimeoutMs?: number;
  /**
   * Maximum messages to retain in conversation history. When exceeded, older
   * messages (excluding the first user message) are truncated to prevent
   * memory pressure in long-running loops.
   *
   * Recommended values:
   *   - Lambda (512MB): 50-100 messages
   *   - Browser agent: 30-50 messages
   *   - Long-running server: 200+ or sliding window
   *
   * Default: undefined (no truncation).
   */
  maxHistoryMessages?: number;
}

// ─── ReactExecutor ──────────────────────────────────────────────────────────

/**
 * Bridge-governed ReAct loop executor with atomic governance.
 *
 * Composes with AnalemmaBridge — does NOT subclass it. The executor owns
 * the LLM call loop; every tool invocation is delegated through bridge.segment().
 */
export class ReactExecutor {
  private readonly bridge: AnalemmaBridge;
  private readonly llmClient: LLMClient;
  private readonly maxIterations: number;
  private readonly maxTokensPerTurn: number;
  private readonly temperature: number;
  private readonly tokenBudget: number;
  private readonly systemPrompt: string;
  private readonly tokenCounter: (text: string) => number;
  private readonly wallClockTimeoutMs: number | null;
  private readonly toolTimeoutMs: number;
  private readonly maxHistoryMessages: number | null;
  private readonly tools: Map<string, RegisteredTool> = new Map();

  private startTime: number | null = null;

  constructor(config: ReactExecutorConfig) {
    this.bridge = config.bridge;
    this.llmClient = config.llmClient;
    this.maxIterations = config.maxIterations ?? 25;
    this.maxTokensPerTurn = config.maxTokensPerTurn ?? 4096;
    this.temperature = config.temperature ?? 0.0;
    this.tokenBudget = config.tokenBudget ?? 500_000;
    this.systemPrompt = config.systemPrompt ?? "You are a helpful assistant.";
    this.tokenCounter = config.tokenCounter ?? ReactExecutor.defaultTokenEstimate;
    this.wallClockTimeoutMs = config.wallClockTimeoutMs ?? null;
    this.toolTimeoutMs = config.toolTimeoutMs ?? 30_000;
    this.maxHistoryMessages = config.maxHistoryMessages ?? null;
  }

  // ── Public API ────────────────────────────────────────────────────────────

  /**
   * Register a tool for the ReAct loop.
   *
   * @param name          Tool name (must match what Claude will call).
   * @param description   Human-readable description for Claude.
   * @param inputSchema   JSON Schema for tool input parameters.
   * @param handler       Callable that executes the tool.
   * @param options       Optional: bridgeAction maps to CAPABILITY_MAP action name.
   */
  registerTool(
    name: string,
    description: string,
    inputSchema: Record<string, unknown>,
    handler: ToolHandler,
    options?: { bridgeAction?: string },
  ): void {
    let schema = inputSchema;
    if (schema.type !== "object") {
      schema = {
        type: "object",
        properties: (schema.properties as Record<string, unknown>) ?? schema,
        required: (schema.required as string[]) ?? [],
      };
    }

    this.tools.set(name, {
      name,
      description,
      inputSchema: schema,
      handler,
      bridgeAction: options?.bridgeAction ?? name,
    });
  }

  /** Return tool definitions in Anthropic Messages API format. */
  getToolDefinitions(): ToolDefinition[] {
    return [...this.tools.values()].map((tool) => ({
      name: tool.name,
      description: tool.description,
      input_schema: tool.inputSchema,
    }));
  }

  /**
   * Execute the full ReAct loop.
   *
   * @param task The user's task description.
   * @returns ReactResult with final answer, conversation history, and metadata.
   */
  async run(task: string): Promise<ReactResult> {
    const messages: ConversationMessage[] = [{ role: "user", content: task }];
    const tools = this.getToolDefinitions();
    const segments: string[] = [];

    let totalInputTokens = 0;
    let totalOutputTokens = 0;
    const rejectionCounter = new Map<string, number>();
    this.startTime = Date.now();

    for (let iteration = 0; iteration < this.maxIterations; iteration++) {
      // ── Budget gate 1/3: pre-loop ──
      const totalTokensUsed = totalInputTokens + totalOutputTokens;
      if (totalTokensUsed >= this.tokenBudget) {
        return this.buildBudgetExceededResult(
          messages, iteration, totalInputTokens, totalOutputTokens, segments,
        );
      }

      // ── Wall-clock gate: pre-LLM ──
      if (this.checkWallClock()) {
        return {
          finalAnswer: "",
          messages,
          iterations: iteration,
          totalInputTokens,
          totalOutputTokens,
          segments,
          stopReason: "wall_clock_timeout",
        };
      }

      // ── LLM call ──
      const response = await this.callLLM(messages, tools);

      // Accumulate usage
      totalInputTokens += response.usage.input_tokens;
      totalOutputTokens += response.usage.output_tokens;

      // ── Budget gate 2/3: post-LLM ──
      const totalAfterLLM = totalInputTokens + totalOutputTokens;
      if (totalAfterLLM >= this.tokenBudget) {
        // If LLM returned a final answer, honor it despite budget
        if (response.stop_reason === "end_turn") {
          const finalText = ReactExecutor.extractText(response);
          await this.sendFinalSegment(finalText, segments);
          return {
            finalAnswer: finalText,
            messages,
            iterations: iteration + 1,
            totalInputTokens,
            totalOutputTokens,
            segments,
            stopReason: "end_turn",
          };
        }
        return this.buildBudgetExceededResult(
          messages, iteration + 1, totalInputTokens, totalOutputTokens, segments,
        );
      }

      // ── Final answer (no tool_use) ──
      const hasToolUse = response.content.some((b) => b.type === "tool_use");

      if (!hasToolUse && response.stop_reason === "end_turn") {
        const finalText = ReactExecutor.extractText(response);
        await this.sendFinalSegment(finalText, segments);
        return {
          finalAnswer: finalText,
          messages,
          iterations: iteration + 1,
          totalInputTokens,
          totalOutputTokens,
          segments,
          stopReason: "end_turn",
        };
      }

      // ── Build assistant message for history ──
      const assistantContent: LLMContentBlock[] = [];
      for (const block of response.content) {
        if (block.type === "text") {
          assistantContent.push({ type: "text", text: block.text });
        } else if (block.type === "tool_use") {
          assistantContent.push({
            type: "tool_use",
            id: block.id,
            name: block.name,
            input: block.input,
          });
        }
      }
      messages.push({ role: "assistant", content: assistantContent });

      // ── Extract thought for bridge context ──
      const thought = ReactExecutor.extractText(response) || `[Iteration ${iteration + 1}]`;

      // ── Atomic governance: process tool batch ──
      const toolUseBlocks = response.content.filter(
        (b): b is Extract<LLMContentBlock, { type: "tool_use" }> =>
          b.type === "tool_use",
      );
      const { toolResults, segmentIds, wasKilled } = await this.processToolBatch(
        toolUseBlocks,
        thought,
        iteration,
        totalInputTokens + totalOutputTokens,
        rejectionCounter,
      );
      segments.push(...segmentIds);
      messages.push({ role: "user", content: toolResults });

      if (wasKilled) {
        return {
          finalAnswer: "",
          messages,
          iterations: iteration + 1,
          totalInputTokens,
          totalOutputTokens,
          segments,
          stopReason: "sigkill",
        };
      }

      // ── Wall-clock gate: post-tool ──
      if (this.checkWallClock()) {
        return {
          finalAnswer: "",
          messages,
          iterations: iteration + 1,
          totalInputTokens,
          totalOutputTokens,
          segments,
          stopReason: "wall_clock_timeout",
        };
      }

      // ── Budget gate 3/3: post-tool estimate ──
      const toolResultText = toolResults
        .map((r) => r.content)
        .join("");
      const estimatedNextInput = this.tokenCounter(toolResultText);
      if (totalAfterLLM + estimatedNextInput >= this.tokenBudget) {
        return this.buildBudgetExceededResult(
          messages, iteration + 1, totalInputTokens, totalOutputTokens, segments,
        );
      }

      // ── Consecutive rejection limit ──
      for (const [toolName, count] of rejectionCounter) {
        if (count >= MAX_CONSECUTIVE_REJECTIONS) {
          return {
            finalAnswer: "",
            messages,
            iterations: iteration + 1,
            totalInputTokens,
            totalOutputTokens,
            segments,
            stopReason: "max_rejections",
          };
        }
      }

      // ── Context window truncation ──
      if (this.maxHistoryMessages !== null && messages.length > this.maxHistoryMessages) {
        // Preserve first user message (task), truncate oldest intermediate messages
        const firstMsg = messages[0];
        const excess = messages.length - this.maxHistoryMessages;
        messages.splice(1, excess);
        // Ensure first message is still the original task
        if (messages[0] !== firstMsg) {
          messages.unshift(firstMsg);
        }
      }
    }

    // Loop exhausted
    return {
      finalAnswer: "",
      messages,
      iterations: this.maxIterations,
      totalInputTokens,
      totalOutputTokens,
      segments,
      stopReason: "max_iterations",
    };
  }

  // ── Private: Budget & Clock ───────────────────────────────────────────────

  private checkWallClock(): boolean {
    if (this.wallClockTimeoutMs === null || this.startTime === null) return false;
    return Date.now() - this.startTime >= this.wallClockTimeoutMs;
  }

  private remainingWallClockMs(): number | null {
    if (this.wallClockTimeoutMs === null || this.startTime === null) return null;
    return Math.max(0, this.wallClockTimeoutMs - (Date.now() - this.startTime));
  }

  static defaultTokenEstimate(text: string): number {
    return Math.floor(text.length / TOKEN_ESTIMATE_CHARS_PER_TOKEN);
  }

  // ── Private: LLM Calling ──────────────────────────────────────────────────

  private async callLLM(
    messages: ConversationMessage[],
    tools: ToolDefinition[],
  ): Promise<LLMResponse> {
    let lastError: Error | null = null;

    // Cap LLM timeout to remaining wall-clock (min 10s, max 120s)
    const remaining = this.remainingWallClockMs();
    const timeoutMs = remaining !== null
      ? Math.min(Math.max(remaining - 5000, 10_000), 120_000)
      : 120_000;

    for (let attempt = 0; attempt < LLM_MAX_RETRIES; attempt++) {
      try {
        return await this.llmClient.createMessage(messages, tools, {
          system: this.systemPrompt,
          max_tokens: this.maxTokensPerTurn,
          temperature: this.temperature > 0 ? this.temperature : undefined,
          timeout_ms: timeoutMs,
        });
      } catch (error) {
        lastError = error instanceof Error ? error : new Error(String(error));
        const errorStr = lastError.message.toLowerCase();
        const isRetryable = [
          "throttl", "timeout", "too many", "rate limit", "overloaded",
        ].some((kw) => errorStr.includes(kw));

        if (isRetryable && attempt < LLM_MAX_RETRIES - 1) {
          const delay = LLM_BASE_DELAY_MS * Math.pow(2, attempt);
          await new Promise((resolve) => setTimeout(resolve, delay));
        } else {
          throw lastError;
        }
      }
    }

    throw lastError!;
  }

  // ── Private: Atomic Governance ────────────────────────────────────────────

  /**
   * Process a batch of parallel tool calls with strictly sequential 3-phase
   * atomic governance.
   *
   * Phase 1 — Governance: probe bridge.segment() for ALL tools before
   *           executing any handler. Uses Promise.all to await all probes.
   *           No tool handlers run in this phase (no-op execute).
   * Phase 2 — Kill check: synchronous scan of all decisions. If ANY tool
   *           received SIGKILL, abort entire batch immediately.
   * Phase 3 — Execution: only entered when Phase 2 passes. Sequential loop
   *           runs approved handlers, builds error results for rejected.
   */
  private async processToolBatch(
    toolUseBlocks: Array<{ id: string; name: string; input: Record<string, unknown> }>,
    thought: string,
    iteration: number,
    totalTokens: number,
    rejectionCounter: Map<string, number>,
  ): Promise<{
    toolResults: ToolResultBlock[];
    segmentIds: string[];
    wasKilled: boolean;
  }> {
    // Phase 1: Governance decisions — ALL probes complete before any execution
    const decisions = await Promise.all(
      toolUseBlocks.map((block) =>
        this.checkToolGovernance(
          block.name,
          block.input,
          block.id,
          thought,
          iteration,
          totalTokens,
        ),
      ),
    );

    const segmentIds = decisions
      .filter((d) => d.checkpointId !== null)
      .map((d) => d.checkpointId!);

    // Phase 2: Atomic kill check — one SIGKILL aborts all
    const killDecision = decisions.find((d) => d.wasKilled);
    if (killDecision) {
      const toolResults: ToolResultBlock[] = toolUseBlocks.map((block) => ({
        type: "tool_result" as const,
        tool_use_id: block.id,
        is_error: true,
        content:
          `ABORTED: Atomic governance violation in batch — ` +
          `${killDecision.reason ?? "Terminated by kernel"}`,
      }));
      return { toolResults, segmentIds, wasKilled: true };
    }

    // Phase 3: Execute approved, error for rejected — strictly sequential
    const toolResults: ToolResultBlock[] = [];
    for (let i = 0; i < toolUseBlocks.length; i++) {
      const block = toolUseBlocks[i];
      const decision = decisions[i];

      if (decision.shouldRollback) {
        rejectionCounter.set(
          block.name,
          (rejectionCounter.get(block.name) ?? 0) + 1,
        );
        toolResults.push({
          type: "tool_result",
          tool_use_id: block.id,
          is_error: true,
          content: `REJECTED by governance kernel: ${decision.reason ?? "Action not allowed"}`,
        });
      } else if (decision.allowed) {
        rejectionCounter.delete(block.name);
        const result = await this.executeToolHandler(
          block.name,
          decision.actionParams,
          block.id,
        );
        toolResults.push(result);
      } else {
        toolResults.push({
          type: "tool_result",
          tool_use_id: block.id,
          is_error: true,
          content: decision.reason ?? `Unexpected governance state for '${block.name}'`,
        });
      }
    }

    return { toolResults, segmentIds, wasKilled: false };
  }

  /**
   * Probe governance for a single tool call via bridge.segment().
   *
   * Uses a no-op execute callback — the bridge sends PROPOSE, gets COMMIT,
   * and calls execute() (which returns null). No handler runs here.
   * The governance decision is read from the SegmentOutcome.
   */
  private async checkToolGovernance(
    toolName: string,
    toolInput: Record<string, unknown>,
    toolUseId: string,
    thought: string,
    iteration: number,
    totalTokens: number,
  ): Promise<GovernanceDecision> {
    const tool = this.tools.get(toolName);
    if (!tool) {
      return {
        allowed: false,
        wasKilled: false,
        shouldRollback: false,
        checkpointId: null,
        reason: `Unknown tool: '${toolName}'. Available: ${[...this.tools.keys()].join(", ")}`,
        actionParams: toolInput,
      };
    }

    const stateSnapshot = {
      token_usage_total: totalTokens,
      react_iteration: iteration,
    };

    try {
      // Governance probe: execute callback is a no-op
      const outcome: SegmentOutcome<null> = await this.bridge.segment<null>({
        thought,
        action: tool.bridgeAction,
        params: toolInput,
        segmentType: "TOOL_CALL" as SegmentType,
        stateSnapshot,
        execute: async () => null,
      });

      const commit = outcome.commit;
      const checkpointId = commit.checkpoint_id;

      if (commit.status === "APPROVED" || commit.status === "MODIFIED") {
        return {
          allowed: true,
          wasKilled: false,
          shouldRollback: false,
          checkpointId,
          reason: null,
          actionParams: commit.commands.action_override ?? toolInput,
        };
      }

      // REJECTED or SOFT_ROLLBACK
      return {
        allowed: false,
        wasKilled: false,
        shouldRollback: true,
        checkpointId,
        reason: outcome.recoveryInstruction ?? "Action not allowed",
        actionParams: toolInput,
      };
    } catch (error) {
      // SecurityViolation = SIGKILL
      if (error instanceof SecurityViolation) {
        return {
          allowed: false,
          wasKilled: true,
          shouldRollback: false,
          checkpointId: null,
          reason: error.message,
          actionParams: toolInput,
        };
      }

      // Network / timeout / unexpected errors → fail-safe deny
      return {
        allowed: false,
        wasKilled: false,
        shouldRollback: true,
        checkpointId: null,
        reason: `Bridge error: ${error instanceof Error ? error.message : String(error)}`,
        actionParams: toolInput,
      };
    }
  }

  /**
   * Execute a tool handler after governance approval.
   *
   * Pre-validates required fields from input_schema before calling the handler.
   * Uses Promise.race for per-tool timeout enforcement.
   */
  private async executeToolHandler(
    toolName: string,
    toolInput: Record<string, unknown>,
    toolUseId: string,
  ): Promise<ToolResultBlock> {
    const tool = this.tools.get(toolName)!;

    // Schema pre-validation — catch LLM hallucination
    const schemaError = ReactExecutor.validateToolInput(tool, toolInput);
    if (schemaError) {
      return {
        type: "tool_result",
        tool_use_id: toolUseId,
        content: `Schema violation for '${toolName}': ${schemaError}. Expected schema: ${JSON.stringify(tool.inputSchema)}`,
        is_error: true,
      };
    }

    try {
      const result = await Promise.race([
        Promise.resolve(tool.handler(toolInput)),
        new Promise<never>((_, reject) =>
          setTimeout(
            () => reject(new Error("TOOL_TIMEOUT")),
            this.toolTimeoutMs,
          ),
        ),
      ]);

      const resultStr =
        typeof result === "string" ? result : JSON.stringify(result);

      return {
        type: "tool_result",
        tool_use_id: toolUseId,
        content: resultStr,
      };
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      if (errMsg === "TOOL_TIMEOUT") {
        return {
          type: "tool_result",
          tool_use_id: toolUseId,
          content: `Tool '${toolName}' timed out after ${this.toolTimeoutMs}ms`,
          is_error: true,
        };
      }
      return {
        type: "tool_result",
        tool_use_id: toolUseId,
        content: `Tool execution failed: ${errMsg}`,
        is_error: true,
      };
    }
  }

  // ── Private: Helpers ──────────────────────────────────────────────────────

  static validateToolInput(
    tool: RegisteredTool,
    toolInput: Record<string, unknown>,
  ): string | null {
    if (typeof toolInput !== "object" || toolInput === null || Array.isArray(toolInput)) {
      return `Expected object input, got ${typeof toolInput}`;
    }

    const required = (tool.inputSchema.required as string[]) ?? [];
    const missing = required.filter((f) => !(f in toolInput));
    if (missing.length > 0) {
      return `Missing required fields: ${JSON.stringify(missing)}`;
    }

    // Lightweight type check for declared properties
    const properties = (tool.inputSchema.properties as Record<string, Record<string, unknown>>) ?? {};
    const typeMap: Record<string, string> = {
      string: "string",
      integer: "number",
      number: "number",
      boolean: "boolean",
    };
    for (const [fieldName, fieldSchema] of Object.entries(properties)) {
      if (!(fieldName in toolInput)) continue;
      const expectedType = typeMap[fieldSchema.type as string];
      if (expectedType && typeof toolInput[fieldName] !== expectedType) {
        return `Field '${fieldName}' expected ${fieldSchema.type}, got ${typeof toolInput[fieldName]}`;
      }
    }

    return null;
  }

  static extractText(response: LLMResponse): string {
    return response.content
      .filter((b): b is Extract<LLMContentBlock, { type: "text" }> => b.type === "text")
      .map((b) => b.text)
      .join("");
  }

  private buildBudgetExceededResult(
    messages: ConversationMessage[],
    iterations: number,
    totalInputTokens: number,
    totalOutputTokens: number,
    segments: string[],
  ): ReactResult {
    return {
      finalAnswer: "",
      messages,
      iterations,
      totalInputTokens,
      totalOutputTokens,
      segments,
      stopReason: "budget_exceeded",
    };
  }

  private async sendFinalSegment(
    finalAnswer: string,
    segments: string[],
  ): Promise<void> {
    try {
      const outcome = await this.bridge.segment<null>({
        thought: `Task complete. Final answer: ${finalAnswer.slice(0, 200)}`,
        action: "read_only",
        params: { final_answer_length: finalAnswer.length },
        segmentType: "FINAL" as SegmentType,
        execute: async () => null,
      });
      if (outcome.commit.checkpoint_id) {
        segments.push(outcome.commit.checkpoint_id);
      }
    } catch {
      // Non-critical — FINAL segment failure doesn't affect the result
    }
  }
}
