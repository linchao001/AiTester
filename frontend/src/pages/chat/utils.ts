import type { ChatSession, PermMode } from "../../api/client";

const DAY_MS = 86_400_000;

/** 原型 :1393-1398 —— CJK ≈ 1 token/字，其余 4 字符 ≈ 1 token。 */
export function estTokens(s: string): number {
  const text = String(s || "");
  const cjk = (text.match(/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]/g) || []).length;
  return Math.round(cjk + (text.length - cjk) / 4);
}

/** 与后端 `services/chat.py` 的 `HISTORY_MAX` 同一常数：两侧口径不一致，meter 就会把
 *  后端根本不会带上的历史算进占用（第 1 片登记、第 2 片转来的欠账，第 4 片裁定 6 收口）。 */
export const HISTORY_MAX = 40;

/** 原型 :1399-1409 —— 系统提示词 + 历史 + 当前输入的占用估算；历史只数最近 HISTORY_MAX 条。 */
export function contextUsage(args: {
  systemPrompt: string;
  history: { content: string }[];
  input: string;
  cap: number;
}): { used: number; cap: number; pct: number } {
  const used = args.history.slice(-HISTORY_MAX).reduce(
    (acc, m) => acc + estTokens(m.content), estTokens(args.systemPrompt)) + estTokens(args.input || "");
  const cap = args.cap > 0 ? args.cap : 0;
  return { used, cap, pct: cap ? Math.min(100, Math.round((used / cap) * 100)) : 0 };
}

/** 本地日历日差：同一天为 0 天，跨天按日历天算（不用 UTC 除法，避免 UTC+8 下午误归「更早」）。 */
function daysSince(ts: number, now: number): number {
  const startOfDay = (t: number) => new Date(t).setHours(0, 0, 0, 0);
  return Math.round((startOfDay(now) - startOfDay(ts)) / DAY_MS);
}

/** 原型 :1257 的三组（存死字符串）换成按 updated_at 现算；空组不出标题。 */
export function groupSessions(
  sessions: ChatSession[],
  now = Date.now(),
): { label: string; items: ChatSession[] }[] {
  const today: ChatSession[] = [];
  const week: ChatSession[] = [];
  const older: ChatSession[] = [];
  for (const s of sessions) {
    const d = daysSince(s.updated_at, now);
    if (d <= 0) today.push(s);
    else if (d < 7) week.push(s);
    else older.push(s);
  }
  return [{ label: "今天", items: today }, { label: "7 天内", items: week }, { label: "更早", items: older }]
    .filter((g) => g.items.length > 0);
}

/** 会话行与 meta 共用：今天只显 HH:MM，更早补 M月D日（原型 now() 恒带日期且补零，此处按日历日收敛成偏离）。 */
export function fmtTime(ts: number, now = Date.now()): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return daysSince(ts, now) <= 0 ? hm : `${d.getMonth() + 1}月${d.getDate()}日 ${hm}`;
}

/** chip 三档元数据：id / label / desc 逐字对齐 spec「事件与前端折叠」表。
 *  原型 :1452-1453 只有两档且 strict 置灰，第 5 片 strict 真的能用了，boundary 是新增档。 */
export const PERM_MODES: { id: PermMode; icon: string; label: string; desc: string }[] = [
  { id: "free", icon: "🛡", label: "自由权限", desc: "所有操作（写文件、执行命令等）自动执行，无需你授权" },
  { id: "boundary", icon: "⚑", label: "只批界外", desc: "项目目录外的写入、以及所有命令执行需你授权" },
  { id: "strict", icon: "🔒", label: "严格权限", desc: "每次写盘 / 执行命令前需你授权" },
];

export const permMeta = (id: PermMode) => PERM_MODES.find((m) => m.id === id) ?? PERM_MODES[0];

/** 档位是视图偏好，与 projectId 同址落 localStorage（走查项 2：刷新后档位保留）。 */
const PERM_STORAGE_KEY = "aitester.chat.permMode";

export function loadPermMode(): PermMode {
  const v = window.localStorage.getItem(PERM_STORAGE_KEY);
  // 脏值、旧值、隐私模式下的 null 一律回 free：默认档零行为是红线，不能靠存储兜住语义
  return v === "boundary" || v === "strict" ? v : "free";
}

export function savePermMode(m: PermMode): void {
  window.localStorage.setItem(PERM_STORAGE_KEY, m);
}
