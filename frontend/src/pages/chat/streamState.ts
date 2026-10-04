import type { ChatStep, KbDraft, StreamEvent } from "../../api/client";

/** done 事件的类型别名：finalize 只吃终态，签名写死比 Extract 更好读。 */
export type DoneEvent = Extract<StreamEvent, { type: "done" }>;

/** 一轮 agent 输出的 live 累积。toolCalled 由该轮的 call 事件置真，
 *  它是「这一轮不是最终答复」的唯一判据（中间轮文本靠它折进过程块）。 */
export interface LiveRound { round: number; text: string; toolCalled: boolean }

/** 已宣告但结果未回的调用：过程块里的 ⏳ 行。 */
export interface PendingCall { key: string; tool: string; round: number; detail: string }

export interface StreamingState {
  runId: string;
  sessionId: string;
  title: string;
  rounds: LiveRound[];
  pending: PendingCall[];
  steps: ChatStep[];
  drafts: KbDraft[];
  stopped: boolean;
  terminal: boolean;     // done/error 已收到：之后的事件一律忽略（终态恒为一条）
  done: DoneEvent | null; // 终态事件本身随状态走——页面不得另设闭包局部变量接终态（TS 对闭包内赋值的收窄不可靠）
  fail: string;          // error 事件的 detail；HTTP 层失败由调用方自己写进界面，不进这里
}

export function newStreamState(): StreamingState {
  return { runId: "", sessionId: "", title: "", rounds: [], pending: [], steps: [], drafts: [],
    stopped: false, terminal: false, done: null, fail: "" };
}

const callKey = (round: number, tool: string) => `${round}::${tool}`;

/** 逐事件折叠，返回新对象（React state 直用；同 key 重复调用按 FIFO 摘第一个，
 *  ToolNode 回 ToolMessage 的顺序与 tool_calls 一致，故同名同轮也不会错配）。 */
export function applyEvent(state: StreamingState, ev: StreamEvent): StreamingState {
  if (state.terminal) return state;
  switch (ev.type) {
    case "start":
      return { ...state, runId: ev.run_id, sessionId: ev.session_id || state.sessionId };
    case "delta": {
      const at = state.rounds.findIndex((r) => r.round === ev.round);
      const rounds = at < 0
        ? [...state.rounds, { round: ev.round, text: ev.text, toolCalled: false }]
        : state.rounds.map((r, i) => (i === at ? { ...r, text: r.text + ev.text } : r));
      return { ...state, rounds };
    }
    case "call": {
      const key = callKey(ev.round, ev.tool);
      return {
        ...state,
        rounds: state.rounds.map((r) => (r.round === ev.round ? { ...r, toolCalled: true } : r)),
        pending: [...state.pending, { key, tool: ev.tool, round: ev.round, detail: ev.detail }],
      };
    }
    case "step": {
      const key = callKey(ev.round, ev.tool);
      const at = state.pending.findIndex((p) => p.key === key);
      return {
        ...state,
        pending: at < 0 ? state.pending : state.pending.filter((_, i) => i !== at),
        steps: [...state.steps, { tool: ev.tool, ok: ev.ok, round: ev.round, detail: ev.detail }],
      };
    }
    case "draft":
      return { ...state, drafts: [...state.drafts, ev.draft] };
    case "done":
      return { ...state, terminal: true, done: ev, stopped: ev.stopped,
        sessionId: ev.session_id || state.sessionId, title: ev.title || state.title };
    case "error":
      return { ...state, terminal: true, fail: ev.detail };   // detail 随状态回给调用方，界面按失败撤气泡
  }
}

export interface FinalizedTurn {
  content: string;
  steps: ChatStep[];
  drafts: KbDraft[];
  sessionId: string;
  title: string;
  stopped: boolean;
}

/** 终态折叠：正文只认服务端 done.reply（与落盘逐字相同），live 文本一律不作为 content。 */
export function finalize(state: StreamingState, done: DoneEvent): FinalizedTurn {
  // 中间轮正文只在界面上活一次：折成 📝 行进过程块。不折进 content——进了就污染复制件，
  // 也会被喂进下一轮 prompt（spec 裁定 4）；落盘行里始终只有 done.reply。
  const notes: ChatStep[] = state.rounds
    .filter((r) => r.toolCalled && r.text)
    .map((r) => ({ tool: "📝", ok: true, round: r.round, detail: r.text }));
  // 残留 pending 是「宣告了但没跑完」的调用：渲染成 ✓/✗ 都是假话，且 done.steps 才是落盘口径，
  // 故随 live 状态一起丢弃（重开会话看到的过程块与这里落库的那份一致）。
  return {
    content: done.reply,
    steps: [...done.steps, ...notes].sort((a, b) => a.round - b.round),
    drafts: state.drafts,
    sessionId: done.session_id || state.sessionId,
    title: done.title || state.title,
    stopped: done.stopped,
  };
}

/** 当前该显示的正文：最后一个有文本的轮次。 */
export function liveText(state: StreamingState | null): string {
  if (!state) return "";
  for (let i = state.rounds.length - 1; i >= 0; i -= 1) {
    if (state.rounds[i].text) return state.rounds[i].text;
  }
  return "";
}

/** 三点占位的判据：连接活着、模型还没开口、也还没有任何过程行。 */
export function isWaiting(state: StreamingState | null): boolean {
  if (!state) return true;
  return state.rounds.length === 0 && state.pending.length === 0 && state.steps.length === 0;
}
