// corral 钩子扩展（pi）：把 pi 的事件翻译成 corral 的事件，往事件文件追加一行。
//
// start 时复制到每个 pi agent 的状态目录，由 pi 用 --extension 加载这一份，不引用 corral 仓库。
// 要求：只用运行时自带的 node:fs，不引入任何依赖；写出的每一行和 hook.py 完全一样（事件格式 1、同样的字段
// 和过滤规则，tests/test_pi_hook.py 核对）；任何情况下都不往标准输出写、不抛异常；没有 CORRAL_EVENTS 时什么都不做。
import { appendFileSync } from "node:fs";

const FORMAT = 1;
const FIELDS = ["session_id", "cwd", "source", "tool_name", "prompt", "last_assistant_message", "notification_type"];

function sessionId(ctx: any): unknown {
  try {
    return ctx?.sessionManager?.getSessionId?.();
  } catch {
    return undefined;
  }
}

function idle(ctx: any): boolean {
  try {
    return ctx?.isIdle?.() === true;
  } catch {
    return false;
  }
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
      has_transcript: true, // pi 没有子会话，事件都属于主会话
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

export default function (pi: any) {
  const inputs: string[] = []; // input 事件的原文（技能、模板展开之前），和 send 送出的文字对得上
  let reply: string | undefined;

  pi.on("session_start", (event: any, ctx: any) => {
    write("SessionStart", ctx, { source: event?.reason });
  });

  pi.on("input", (event: any) => {
    if (typeof event?.text !== "string") return;
    if (!event.streamingBehavior) inputs.length = 0; // 空闲时的新输入：之前被其他扩展拦下的不再留着
    inputs.push(event.text);
  });

  pi.on("before_agent_start", (event: any, ctx: any) => {
    reply = undefined;
    const prompt = inputs.length > 0 ? inputs.shift() : event?.prompt;
    write("UserPromptSubmit", ctx, { prompt });
  });

  pi.on("tool_execution_start", (event: any, ctx: any) => {
    write("PreToolUse", ctx, { tool_name: event?.toolName });
  });

  pi.on("tool_execution_end", (event: any, ctx: any) => {
    write("PostToolUse", ctx, { tool_name: event?.toolName });
  });

  pi.on("ui_prompt_start", (_event: any, ctx: any) => {
    write("Notification", ctx, { notification_type: "permission_prompt" });
  });

  pi.on("ui_prompt_end", (_event: any, ctx: any) => {
    // 还在干活：回到 working；已经空闲（比如人自己调出的框）：回到 idle，不带回复
    write(idle(ctx) ? "Stop" : "PostToolUse", ctx);
  });

  pi.on("message_end", (event: any) => {
    const message = event?.message;
    if (message?.role !== "assistant" || !Array.isArray(message.content)) return;
    const text = message.content
      .filter((part: any) => part?.type === "text" && typeof part.text === "string")
      .map((part: any) => part.text)
      .join("\n");
    if (text) reply = text;
  });

  pi.on("agent_settled", (_event: any, ctx: any) => {
    write("Stop", ctx, { last_assistant_message: reply });
  });

  pi.on("session_shutdown", (event: any, ctx: any) => {
    if (!event?.reason || event.reason === "quit") write("SessionEnd", ctx);
  });
}
