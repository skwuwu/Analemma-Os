/**
 * ReactExecutor unit tests — mirrors Python test_react_executor.py coverage.
 *
 * All tests use mocked AnalemmaBridge and LLMClient:
 *   - MockLLMClient: returns pre-configured LLMResponse[] in sequence
 *   - mockBridge: jest.fn()-based AnalemmaBridge that returns configurable
 *     SegmentOutcome or throws SecurityViolation
 */

import {
  ReactExecutor,
  LLMClient,
  LLMResponse,
  LLMCallOptions,
  ConversationMessage,
  ToolDefinition,
  ToolResultBlock,
  ReactResult,
} from "../react_executor";
import { SecurityViolation, SegmentOutcome } from "../bridge";
import { SegmentCommit } from "../types";

// ─── Mock Infrastructure ────────────────────────────────────────────────────

class MockLLMClient implements LLMClient {
  responses: LLMResponse[] = [];
  callCount = 0;
  calls: Array<{
    messages: ConversationMessage[];
    tools: ToolDefinition[];
    options: LLMCallOptions;
  }> = [];

  async createMessage(
    messages: ConversationMessage[],
    tools: ToolDefinition[],
    options: LLMCallOptions,
  ): Promise<LLMResponse> {
    this.calls.push({ messages, tools, options });
    if (this.callCount >= this.responses.length) {
      throw new Error("MockLLMClient: no more responses configured");
    }
    return this.responses[this.callCount++];
  }
}

function makeTextResponse(text: string, tokens = 100): LLMResponse {
  return {
    content: [{ type: "text", text }],
    stop_reason: "end_turn",
    usage: { input_tokens: tokens, output_tokens: tokens },
  };
}

function makeToolResponse(
  tools: Array<{ id: string; name: string; input: Record<string, unknown> }>,
  thought = "",
  tokens = 100,
): LLMResponse {
  const content = [];
  if (thought) {
    content.push({ type: "text" as const, text: thought });
  }
  for (const t of tools) {
    content.push({
      type: "tool_use" as const,
      id: t.id,
      name: t.name,
      input: t.input,
    });
  }
  return {
    content,
    stop_reason: "tool_use",
    usage: { input_tokens: tokens, output_tokens: tokens },
  };
}

function makeApprovedCommit(checkpointId = "cp_001"): SegmentCommit {
  return {
    protocol_version: "1.0",
    op: "SEGMENT_COMMIT",
    status: "APPROVED",
    checkpoint_id: checkpointId,
    commands: { action_override: null, inject_recovery_instruction: null },
    governance_feedback: { warnings: [], anomaly_score: 0, article_violations: [] },
  };
}

function makeRejectedCommit(
  reason: string,
  checkpointId = "cp_rej",
): SegmentCommit {
  return {
    protocol_version: "1.0",
    op: "SEGMENT_COMMIT",
    status: "REJECTED",
    checkpoint_id: checkpointId,
    commands: {
      action_override: null,
      inject_recovery_instruction: reason,
    },
    governance_feedback: { warnings: [], anomaly_score: 0.8, article_violations: [] },
  };
}

function makeModifiedCommit(
  override: Record<string, unknown>,
  checkpointId = "cp_mod",
): SegmentCommit {
  return {
    protocol_version: "1.0",
    op: "SEGMENT_COMMIT",
    status: "MODIFIED",
    checkpoint_id: checkpointId,
    commands: {
      action_override: override,
      inject_recovery_instruction: null,
    },
    governance_feedback: { warnings: [], anomaly_score: 0, article_violations: [] },
  };
}

function makeMockBridge(
  segmentBehavior: "approved" | "rejected" | "sigkill" | SegmentCommit | SegmentCommit[] = "approved",
): any {
  let callIndex = 0;

  const segmentFn = jest.fn(async (options: any) => {
    // Determine which commit to use
    let commit: SegmentCommit;

    if (segmentBehavior === "sigkill") {
      throw new SecurityViolation("SIGKILL: Terminated by kernel");
    } else if (segmentBehavior === "approved") {
      commit = makeApprovedCommit(`cp_${callIndex++}`);
    } else if (segmentBehavior === "rejected") {
      commit = makeRejectedCommit("Action not allowed", `cp_rej_${callIndex++}`);
    } else if (Array.isArray(segmentBehavior)) {
      const behavior = segmentBehavior[callIndex++];
      if (!behavior) {
        commit = makeApprovedCommit(`cp_${callIndex}`);
      } else {
        commit = behavior;
      }
    } else {
      commit = segmentBehavior;
    }

    if (commit.status === "SIGKILL") {
      throw new SecurityViolation("SIGKILL: Terminated by kernel");
    }

    // Call the execute callback (no-op for governance probes)
    const approvedParams = commit.commands.action_override ?? options.params;
    let result = null;
    if (commit.status === "APPROVED" || commit.status === "MODIFIED") {
      result = await options.execute(approvedParams);
    }

    return {
      result,
      commit,
      recoveryInstruction: commit.commands.inject_recovery_instruction,
    } as SegmentOutcome<any>;
  });

  return { segment: segmentFn };
}

function createExecutor(
  llmClient: MockLLMClient,
  bridge: any,
  overrides: Partial<{
    maxIterations: number;
    tokenBudget: number;
    wallClockTimeoutMs: number;
    toolTimeoutMs: number;
    tokenCounter: (text: string) => number;
    maxHistoryMessages: number;
    systemPrompt: string;
  }> = {},
): ReactExecutor {
  return new ReactExecutor({
    bridge,
    llmClient,
    maxIterations: overrides.maxIterations ?? 25,
    tokenBudget: overrides.tokenBudget ?? 500_000,
    wallClockTimeoutMs: overrides.wallClockTimeoutMs,
    toolTimeoutMs: overrides.toolTimeoutMs ?? 30_000,
    tokenCounter: overrides.tokenCounter,
    maxHistoryMessages: overrides.maxHistoryMessages,
    systemPrompt: overrides.systemPrompt,
  });
}

const echoTool = {
  name: "echo",
  description: "Echo the input text",
  schema: {
    type: "object",
    properties: { text: { type: "string" } },
    required: ["text"],
  },
  handler: (params: Record<string, unknown>) => `echo: ${params.text}`,
};

// ─── Tests ──────────────────────────────────────────────────────────────────

describe("ReactExecutor", () => {
  // 1. Final answer with no tools
  test("final_answer_no_tools", async () => {
    const llm = new MockLLMClient();
    llm.responses = [makeTextResponse("The answer is 42.")];

    const bridge = makeMockBridge("approved");
    const executor = createExecutor(llm, bridge);

    const result = await executor.run("What is the meaning of life?");

    expect(result.stopReason).toBe("end_turn");
    expect(result.finalAnswer).toBe("The answer is 42.");
    expect(result.iterations).toBe(1);
    expect(result.totalInputTokens).toBe(100);
    expect(result.totalOutputTokens).toBe(100);
    // Final segment sent
    expect(bridge.segment).toHaveBeenCalled();
  });

  // 2. Single tool call then answer
  test("single_tool_then_answer", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse(
        [{ id: "tu_1", name: "echo", input: { text: "hello" } }],
        "I will echo hello.",
      ),
      makeTextResponse("Done. The echo returned: echo: hello"),
    ];

    const bridge = makeMockBridge("approved");
    const executor = createExecutor(llm, bridge);
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );

    const result = await executor.run("Echo hello");

    expect(result.stopReason).toBe("end_turn");
    expect(result.iterations).toBe(2);
    expect(result.segments.length).toBeGreaterThanOrEqual(1);
    // Verify tool result was fed back to LLM
    const secondCall = llm.calls[1];
    expect(secondCall).toBeDefined();
    const userMsg = secondCall.messages[secondCall.messages.length - 1];
    expect(userMsg.role).toBe("user");
  });

  // 3. Multi-tool sequential
  test("multi_tool_sequential", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([{ id: "tu_1", name: "echo", input: { text: "first" } }]),
      makeToolResponse([{ id: "tu_2", name: "echo", input: { text: "second" } }]),
      makeTextResponse("All done."),
    ];

    const bridge = makeMockBridge("approved");
    const executor = createExecutor(llm, bridge);
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );

    const result = await executor.run("Echo twice");

    expect(result.stopReason).toBe("end_turn");
    expect(result.iterations).toBe(3);
    // Messages: user, assistant(tool_use), user(tool_result), assistant(tool_use), user(tool_result), ...
    expect(result.messages.length).toBeGreaterThanOrEqual(5);
  });

  // 4. Parallel tool calls
  test("parallel_tool_calls", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([
        { id: "tu_a", name: "echo", input: { text: "alpha" } },
        { id: "tu_b", name: "echo", input: { text: "beta" } },
      ]),
      makeTextResponse("Both echoes done."),
    ];

    const bridge = makeMockBridge("approved");
    const executor = createExecutor(llm, bridge);
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );

    const result = await executor.run("Echo alpha and beta");

    expect(result.stopReason).toBe("end_turn");
    expect(result.iterations).toBe(2);
    // bridge.segment called for both governance probes + final segment
    expect(bridge.segment).toHaveBeenCalledTimes(3); // 2 probes + 1 final
  });

  // 5. Max iterations reached
  test("max_iterations_reached", async () => {
    const llm = new MockLLMClient();
    // Always return tool call — never stops
    for (let i = 0; i < 5; i++) {
      llm.responses.push(
        makeToolResponse([{ id: `tu_${i}`, name: "echo", input: { text: `iter_${i}` } }]),
      );
    }

    const bridge = makeMockBridge("approved");
    const executor = createExecutor(llm, bridge, { maxIterations: 3 });
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );

    const result = await executor.run("Echo forever");

    expect(result.stopReason).toBe("max_iterations");
    expect(result.iterations).toBe(3);
  });

  // 6. Token budget exceeded post-LLM
  test("token_budget_exceeded_post_llm", async () => {
    const llm = new MockLLMClient();
    // First response uses huge token count
    llm.responses = [
      {
        content: [
          { type: "tool_use", id: "tu_1", name: "echo", input: { text: "x" } },
        ],
        stop_reason: "tool_use",
        usage: { input_tokens: 300_000, output_tokens: 300_000 },
      },
    ];

    const bridge = makeMockBridge("approved");
    const executor = createExecutor(llm, bridge, { tokenBudget: 500_000 });
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );

    const result = await executor.run("Do something expensive");

    expect(result.stopReason).toBe("budget_exceeded");
  });

  // 7. Budget exceeded but LLM gave end_turn — honor it
  test("budget_honors_final_answer", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      {
        content: [{ type: "text", text: "Final answer despite budget." }],
        stop_reason: "end_turn",
        usage: { input_tokens: 400_000, output_tokens: 200_000 },
      },
    ];

    const bridge = makeMockBridge("approved");
    const executor = createExecutor(llm, bridge, { tokenBudget: 500_000 });

    const result = await executor.run("Do something");

    expect(result.stopReason).toBe("end_turn");
    expect(result.finalAnswer).toBe("Final answer despite budget.");
  });

  // 8. Post-tool budget estimate
  test("post_tool_budget_estimate", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse(
        [{ id: "tu_1", name: "echo", input: { text: "x" } }],
        "thinking",
        200_000, // 200K tokens used
      ),
    ];

    const bridge = makeMockBridge("approved");
    // Tool returns a very large result
    const bigHandler = () => "x".repeat(2_000_000); // ~500K tokens estimated
    const executor = createExecutor(llm, bridge, { tokenBudget: 500_000 });
    executor.registerTool("echo", "Echo", echoTool.schema, bigHandler);

    const result = await executor.run("Generate large output");

    expect(result.stopReason).toBe("budget_exceeded");
  });

  // 9. Bridge rollback recovery
  test("bridge_rollback_recovery", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([{ id: "tu_1", name: "echo", input: { text: "forbidden" } }]),
      makeTextResponse("OK, I understand."),
    ];

    const rejectedCommit = makeRejectedCommit("Forbidden action detected");
    const bridge = makeMockBridge([rejectedCommit, makeApprovedCommit("cp_final")]);
    const executor = createExecutor(llm, bridge);
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );

    const result = await executor.run("Try forbidden");

    expect(result.stopReason).toBe("end_turn");
    // The tool result should be an error with the rejection reason
    const toolResultMsg = result.messages.find(
      (m) =>
        m.role === "user" &&
        Array.isArray(m.content) &&
        (m.content as ToolResultBlock[]).some(
          (c) => c.is_error && c.content.includes("REJECTED"),
        ),
    );
    expect(toolResultMsg).toBeDefined();
  });

  // 10. Bridge SIGKILL
  test("bridge_sigkill", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([{ id: "tu_1", name: "echo", input: { text: "dangerous" } }]),
    ];

    const bridge = makeMockBridge("sigkill");
    const executor = createExecutor(llm, bridge);
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );

    const result = await executor.run("Do something dangerous");

    expect(result.stopReason).toBe("sigkill");
    expect(result.iterations).toBe(1);
  });

  // 11. Atomic SIGKILL aborts batch
  test("atomic_sigkill_aborts_batch", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([
        { id: "tu_safe", name: "echo", input: { text: "safe" } },
        { id: "tu_kill", name: "danger", input: { text: "kill" } },
      ]),
    ];

    let safeHandlerCalled = false;
    const safeHandler = () => {
      safeHandlerCalled = true;
      return "safe result";
    };
    const dangerHandler = () => "should not run";

    // First probe: APPROVED, second probe: SIGKILL
    const sigkillCommit: SegmentCommit = {
      protocol_version: "1.0",
      op: "SEGMENT_COMMIT",
      status: "SIGKILL",
      checkpoint_id: "cp_kill",
      commands: { action_override: null, inject_recovery_instruction: "Terminated" },
      governance_feedback: { warnings: [], anomaly_score: 1.0, article_violations: [] },
    };
    const bridge = makeMockBridge([makeApprovedCommit("cp_safe"), sigkillCommit]);
    const executor = createExecutor(llm, bridge);
    executor.registerTool("echo", "Safe tool", echoTool.schema, safeHandler);
    executor.registerTool(
      "danger",
      "Dangerous tool",
      { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
      dangerHandler,
    );

    const result = await executor.run("Do both");

    expect(result.stopReason).toBe("sigkill");
    // CRITICAL: safe handler must NOT have been called (atomic governance)
    expect(safeHandlerCalled).toBe(false);
    // All tool results should be ABORTED
    const lastUserMsg = result.messages[result.messages.length - 1];
    expect(Array.isArray(lastUserMsg.content)).toBe(true);
    const toolResults = lastUserMsg.content as ToolResultBlock[];
    expect(toolResults.every((r) => r.is_error)).toBe(true);
    expect(toolResults.every((r) => r.content.includes("ABORTED"))).toBe(true);
  });

  // 12. Atomic mixed decisions
  test("atomic_mixed_decisions", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([
        { id: "tu_ok", name: "echo", input: { text: "allowed" } },
        { id: "tu_no", name: "restricted", input: { text: "blocked" } },
      ]),
      makeTextResponse("Understood, restricted was blocked."),
    ];

    const bridge = makeMockBridge([
      makeApprovedCommit("cp_ok"),
      makeRejectedCommit("Not allowed at this ring level", "cp_no"),
      makeApprovedCommit("cp_final"),
    ]);
    const executor = createExecutor(llm, bridge);
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );
    executor.registerTool(
      "restricted",
      "Restricted tool",
      { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
      () => "should execute only if approved",
    );

    const result = await executor.run("Use both tools");

    expect(result.stopReason).toBe("end_turn");
    // First user message with tool results should have one success and one error
    const toolResultMsg = result.messages.find(
      (m) =>
        m.role === "user" &&
        Array.isArray(m.content) &&
        (m.content as ToolResultBlock[]).length === 2,
    );
    expect(toolResultMsg).toBeDefined();
    const results = toolResultMsg!.content as ToolResultBlock[];
    const okResult = results.find((r) => r.tool_use_id === "tu_ok");
    const noResult = results.find((r) => r.tool_use_id === "tu_no");
    expect(okResult?.is_error).toBeFalsy();
    expect(noResult?.is_error).toBe(true);
    expect(noResult?.content).toContain("REJECTED");
  });

  // 13. Unknown tool
  test("unknown_tool", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([{ id: "tu_1", name: "nonexistent", input: {} }]),
      makeTextResponse("I see, that tool does not exist."),
    ];

    const bridge = makeMockBridge("approved");
    const executor = createExecutor(llm, bridge);
    // No tools registered

    const result = await executor.run("Use nonexistent");

    expect(result.stopReason).toBe("end_turn");
    // Tool result should be error mentioning unknown tool
    const toolResultMsg = result.messages.find(
      (m) =>
        m.role === "user" &&
        Array.isArray(m.content) &&
        (m.content as ToolResultBlock[]).some(
          (c) => c.is_error && c.content.includes("Unknown tool"),
        ),
    );
    expect(toolResultMsg).toBeDefined();
  });

  // 14. Schema validation failure
  test("schema_validation_failure", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([{ id: "tu_1", name: "echo", input: {} }]), // Missing required "text"
      makeTextResponse("I see, missing field."),
    ];

    const bridge = makeMockBridge("approved");
    const executor = createExecutor(llm, bridge);
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );

    const result = await executor.run("Echo without params");

    expect(result.stopReason).toBe("end_turn");
    const toolResultMsg = result.messages.find(
      (m) =>
        m.role === "user" &&
        Array.isArray(m.content) &&
        (m.content as ToolResultBlock[]).some(
          (c) => c.is_error && c.content.includes("Missing required"),
        ),
    );
    expect(toolResultMsg).toBeDefined();
  });

  // 15. Tool timeout
  test("tool_timeout", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([{ id: "tu_1", name: "slow", input: { text: "wait" } }]),
      makeTextResponse("Tool timed out, understood."),
    ];

    const bridge = makeMockBridge("approved");
    const slowHandler = () =>
      new Promise((resolve) => setTimeout(() => resolve("done"), 5000));

    const executor = createExecutor(llm, bridge, { toolTimeoutMs: 50 });
    executor.registerTool(
      "slow",
      "Slow tool",
      { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
      slowHandler,
    );

    const result = await executor.run("Run slow tool");

    expect(result.stopReason).toBe("end_turn");
    const toolResultMsg = result.messages.find(
      (m) =>
        m.role === "user" &&
        Array.isArray(m.content) &&
        (m.content as ToolResultBlock[]).some(
          (c) => c.is_error && c.content.includes("timed out"),
        ),
    );
    expect(toolResultMsg).toBeDefined();
  }, 10_000);

  // 16. Tool execution error
  test("tool_execution_error", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([{ id: "tu_1", name: "broken", input: { text: "crash" } }]),
      makeTextResponse("Tool threw an error."),
    ];

    const bridge = makeMockBridge("approved");
    const brokenHandler = () => {
      throw new Error("BOOM");
    };
    const executor = createExecutor(llm, bridge);
    executor.registerTool(
      "broken",
      "Broken tool",
      { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
      brokenHandler,
    );

    const result = await executor.run("Run broken tool");

    expect(result.stopReason).toBe("end_turn");
    const toolResultMsg = result.messages.find(
      (m) =>
        m.role === "user" &&
        Array.isArray(m.content) &&
        (m.content as ToolResultBlock[]).some(
          (c) => c.is_error && c.content.includes("BOOM"),
        ),
    );
    expect(toolResultMsg).toBeDefined();
  });

  // 17. Wall-clock timeout
  test("wall_clock_timeout", async () => {
    const llm = new MockLLMClient();
    // Provide enough responses so the loop doesn't exhaust them
    for (let i = 0; i < 10; i++) {
      llm.responses.push(
        makeToolResponse([{ id: `tu_${i}`, name: "slow", input: { text: `${i}` } }]),
      );
    }

    const bridge = makeMockBridge("approved");
    // Tool handler introduces a delay so wall-clock reliably expires
    const slowHandler = async (params: Record<string, unknown>) => {
      await new Promise((r) => setTimeout(r, 50));
      return `echo: ${params.text}`;
    };

    const executor = createExecutor(llm, bridge, {
      wallClockTimeoutMs: 30,
      maxIterations: 10,
    });
    executor.registerTool(
      "slow",
      "Slow echo",
      { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
      slowHandler,
    );

    const result = await executor.run("Echo");

    expect(result.stopReason).toBe("wall_clock_timeout");
  }, 10_000);

  // 18. Max consecutive rejections
  test("max_consecutive_rejections", async () => {
    const llm = new MockLLMClient();
    // Keep trying the same tool
    for (let i = 0; i < 5; i++) {
      llm.responses.push(
        makeToolResponse([{ id: `tu_${i}`, name: "echo", input: { text: `try_${i}` } }]),
      );
    }

    // All rejections
    const rejections = [];
    for (let i = 0; i < 5; i++) {
      rejections.push(makeRejectedCommit("Still not allowed", `cp_rej_${i}`));
    }
    const bridge = makeMockBridge(rejections);
    const executor = createExecutor(llm, bridge);
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );

    const result = await executor.run("Keep trying");

    expect(result.stopReason).toBe("max_rejections");
    expect(result.iterations).toBe(3); // MAX_CONSECUTIVE_REJECTIONS = 3
  });

  // 19. Schema normalization
  test("schema_normalization", async () => {
    const executor = createExecutor(new MockLLMClient(), makeMockBridge());

    // Register with flat schema (no type: "object" wrapper)
    executor.registerTool(
      "flat",
      "Flat schema tool",
      { properties: { name: { type: "string" } }, required: ["name"] } as any,
      () => "ok",
    );

    const defs = executor.getToolDefinitions();
    expect(defs[0].input_schema.type).toBe("object");
    expect(defs[0].input_schema.properties).toEqual({ name: { type: "string" } });
    expect(defs[0].input_schema.required).toEqual(["name"]);
  });

  // 20. Custom token counter
  test("custom_token_counter", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse(
        [{ id: "tu_1", name: "echo", input: { text: "x" } }],
        "",
        200_000,
      ),
    ];

    const bridge = makeMockBridge("approved");
    // Custom counter that counts every character as 1 token
    const charCounter = (text: string) => text.length;

    const handler = () => "x".repeat(300_001); // 300K+ "tokens" by char counter
    const executor = createExecutor(llm, bridge, {
      tokenBudget: 500_000,
      tokenCounter: charCounter,
    });
    executor.registerTool("echo", "Echo", echoTool.schema, handler);

    const result = await executor.run("Generate output");

    // 400K from LLM + 300K from tool result > 500K budget
    expect(result.stopReason).toBe("budget_exceeded");
  });

  // 21. Context window truncation
  test("context_window_truncation", async () => {
    const llm = new MockLLMClient();
    // 5 iterations of tool use, then final answer
    for (let i = 0; i < 5; i++) {
      llm.responses.push(
        makeToolResponse([{ id: `tu_${i}`, name: "echo", input: { text: `msg_${i}` } }]),
      );
    }
    llm.responses.push(makeTextResponse("All done."));

    const bridge = makeMockBridge("approved");
    // maxHistoryMessages=4 means after truncation, only 4 messages remain
    const executor = createExecutor(llm, bridge, {
      maxHistoryMessages: 4,
      maxIterations: 10,
    });
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );

    const result = await executor.run("Echo 5 times");

    expect(result.stopReason).toBe("end_turn");
    // With maxHistoryMessages=4, the messages array should have been truncated
    // during the loop. The final result will have the truncated conversation.
    // First message should always be the original user task
    expect(result.messages[0].role).toBe("user");
    expect(result.messages[0].content).toBe("Echo 5 times");
  });

  // ── Additional edge cases ─────────────────────────────────────────────────

  test("modified_commit_uses_action_override", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([{ id: "tu_1", name: "echo", input: { text: "original" } }]),
      makeTextResponse("Done with modified params."),
    ];

    let capturedInput: Record<string, unknown> | null = null;
    const capturingHandler = (params: Record<string, unknown>) => {
      capturedInput = params;
      return `echo: ${params.text}`;
    };

    const modifiedCommit = makeModifiedCommit(
      { text: "modified_by_kernel" },
      "cp_mod_1",
    );
    const bridge = makeMockBridge([modifiedCommit, makeApprovedCommit("cp_final")]);
    const executor = createExecutor(llm, bridge);
    executor.registerTool("echo", "Echo", echoTool.schema, capturingHandler);

    const result = await executor.run("Echo something");

    expect(result.stopReason).toBe("end_turn");
    // Handler should have received the kernel-modified params
    expect(capturedInput).toEqual({ text: "modified_by_kernel" });
  });

  test("bridge_network_error_denies_execution", async () => {
    const llm = new MockLLMClient();
    llm.responses = [
      makeToolResponse([{ id: "tu_1", name: "echo", input: { text: "test" } }]),
      makeTextResponse("OK"),
    ];

    const bridge = {
      segment: jest.fn(async () => {
        throw new Error("ECONNREFUSED");
      }),
    };
    const executor = createExecutor(llm, bridge);
    executor.registerTool(
      echoTool.name,
      echoTool.description,
      echoTool.schema,
      echoTool.handler,
    );

    const result = await executor.run("Try with broken bridge");

    expect(result.stopReason).toBe("end_turn");
    // Tool result should be error from bridge failure
    const toolResultMsg = result.messages.find(
      (m) =>
        m.role === "user" &&
        Array.isArray(m.content) &&
        (m.content as ToolResultBlock[]).some(
          (c) => c.is_error && c.content.includes("Bridge error"),
        ),
    );
    expect(toolResultMsg).toBeDefined();
  });
});
