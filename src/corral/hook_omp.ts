// corral 钩子扩展（omp）：把 omp 的事件翻译成 corral 的事件，往事件文件追加一行。
//
// start 时复制到每个 omp agent 的状态目录，由 omp 用 --extension 加载这一份，不引用 corral 仓库。
// 要求：只用运行时自带的 node:fs，不引入任何依赖；写出的每一行和 hook.py 完全一样（事件格式 1、同样的字段
// 和过滤规则，tests/test_omp_hook.py 核对）；任何情况下都不往标准输出写、不抛异常；没有 CORRAL_EVENTS 时什么都不做。
//
// 和 pi 的区别（DESIGN 第 12 节）：omp 没有 agent_settled，回合结束要从 agent_end 判断——willContinue 为真的不算，
// 可重试的出错多等一会儿看会不会重试，其余去抖一小段；期间收到新一轮就取消。omp 有子会话，只写有界面的主会话。
import { appendFileSync } from "node:fs";

const FORMAT = 1;
const FIELDS = ["session_id", "cwd", "source", "tool_name", "prompt", "last_assistant_message", "notification_type"];
const IDLE_DEBOUNCE_MS = 250;
const RETRY_GRACE_MS = 2500;
const RETRYABLE =
  /overloaded|rate.?limit|too many requests|\b(429|500|502|503|504)\b|unavailable|server.?error|internal.?error|network|connection|socket|fetch failed|timed? ?out|timeout|terminated/i;

function sessionId(ctx: any): unknown {
  try {
    return ctx?.sessionManager?.getSessionId?.();
  } catch {
    return undefined;
  }
}

function isRoot(ctx: any): boolean {
  return ctx?.hasUI === true;
}

function write(ev: string, ctx: any, fields: Record<string, unknown> = {}): void {
  try {
    const path = process.env.CORRAL_EVENTS;
    if (!path) return;
    const record: Record<string, unknown> = {
      v: FORMAT,
      t: Date.now() / 1000,
      ev,
      inst: process.env.CORRAL_INSTANCE ?? "",
      has_transcript: true, // 只有主会话的事件会写到这里
    };
    const all: Record<string, unknown> = { session_id: sessionId(ctx), cwd: ctx?.cwd, ...fields };
    for (const key of FIELDS) {
      const value = all[key];
      if (typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && isFinite(value))) {
        record[key] = value;
      }
    }
    // 时间总写成小数，和 hook.py 的 time.time() 一致
    const line = JSON.stringify(record).replace(/"t":(\d+)([,}])/, '"t":$1.0$2');
    appendFileSync(path, line + "\n", { mode: 0o600 });
  } catch {
    // 写不进去就算了：钩子出错不能影响 agent
  }
}

function retryable(event: any): boolean {
  const messages = Array.isArray(event?.messages) ? event.messages : [];
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (message?.role !== "assistant") continue;
    return message.stopReason === "error" && RETRYABLE.test(String(message.errorMessage ?? ""));
  }
  return false;
}

export default function (pi: any) {
  const inputs: string[] = []; // input 事件的原文，和 send 送出的文字对得上
  let reply: string | undefined;
  let pendingStop: ReturnType<typeof setTimeout> | undefined;

  function cancelStop(): void {
    if (pendingStop) clearTimeout(pendingStop);
    pendingStop = undefined;
  }

  function scheduleStop(ctx: any, delayMs: number): void {
    cancelStop();
    pendingStop = setTimeout(() => {
      pendingStop = undefined;
      write("Stop", ctx, { last_assistant_message: reply });
    }, delayMs);
    (pendingStop as any)?.unref?.();
  }

  pi.on("session_start", (event: any, ctx: any) => {
    if (!isRoot(ctx)) return;
    write("SessionStart", ctx, { source: event?.reason ?? "startup" });
  });

  pi.on("session_switch", (event: any, ctx: any) => {
    if (!isRoot(ctx)) return;
    cancelStop();
    write("SessionStart", ctx, { source: event?.reason ?? "resume" });
  });

  pi.on("input", (event: any, ctx: any) => {
    if (!isRoot(ctx) || typeof event?.text !== "string") return;
    if (!event.streamingBehavior) inputs.length = 0;
    inputs.push(event.text);
  });

  pi.on("before_agent_start", (event: any, ctx: any) => {
    if (!isRoot(ctx)) return;
    cancelStop();
    reply = undefined;
    const prompt = inputs.length > 0 ? inputs.shift() : event?.prompt;
    write("UserPromptSubmit", ctx, { prompt });
  });

  pi.on("agent_start", (_event: any, ctx: any) => {
    if (!isRoot(ctx)) return;
    cancelStop(); // 自动继续、重试：上一轮还没真正结束
  });

  pi.on("tool_execution_start", (event: any, ctx: any) => {
    if (!isRoot(ctx)) return;
    if (event?.toolName === "ask") {
      write("Notification", ctx, { notification_type: "permission_prompt" }); // 提问工具：等人回答
    } else {
      write("PreToolUse", ctx, { tool_name: event?.toolName });
    }
  });

  pi.on("tool_execution_end", (event: any, ctx: any) => {
    if (!isRoot(ctx)) return;
    write("PostToolUse", ctx, { tool_name: event?.toolName });
  });

  pi.on("tool_approval_requested", (_event: any, ctx: any) => {
    if (!isRoot(ctx)) return;
    write("Notification", ctx, { notification_type: "permission_prompt" });
  });

  pi.on("tool_approval_resolved", (event: any, ctx: any) => {
    if (!isRoot(ctx)) return;
    write("PostToolUse", ctx, { tool_name: event?.toolName });
  });

  pi.on("message_end", (event: any, ctx: any) => {
    if (!isRoot(ctx)) return;
    const message = event?.message;
    if (message?.role !== "assistant" || !Array.isArray(message.content)) return;
    const text = message.content
      .filter((part: any) => part?.type === "text" && typeof part.text === "string")
      .map((part: any) => part.text)
      .join("\n");
    if (text) reply = text;
  });

  pi.on("agent_end", (event: any, ctx: any) => {
    if (!isRoot(ctx)) return;
    if (event?.willContinue === true) return;
    scheduleStop(ctx, retryable(event) ? RETRY_GRACE_MS : IDLE_DEBOUNCE_MS);
  });

  pi.on("session_shutdown", (_event: any, ctx: any) => {
    if (!isRoot(ctx)) return;
    cancelStop();
    write("SessionEnd", ctx);
  });
}
