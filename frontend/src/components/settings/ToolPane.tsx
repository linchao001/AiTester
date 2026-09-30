import { Fragment, useState } from "react";
import {
  putToolEnabled,
  type CapabilityResponse,
  type ToolInfo,
} from "../../api/client";

interface ToolPaneProps {
  tools: ToolInfo[];
  caps: CapabilityResponse;
  saving: boolean;
  onAction: (action: () => Promise<CapabilityResponse>) => void;
}

// caps 在 Props 上声明但本面板只消费 tools：启停级联真相在后端，
// 前端无 agent 列表可交叉校验，保留入参以对齐外壳 runCaps 刷新回路。
export default function ToolPane({ tools, saving, onAction }: ToolPaneProps) {
  const [tip, setTip] = useState<string | null>(null);

  // 按 group 归并且保持后端目录原始顺序：「自定义 → 智能体 → 系统」在本期
  // 即「文件处理工具 → 命令执行工具 → 网页搜索工具」。不做字母/状态排序——
  // 重排会把分组打散。
  const groups = tools.reduce<Record<string, { label: string; tools: ToolInfo[] }>>(
    (acc, t, idx) => {
      const isGroupFirst = idx === 0 || tools[idx - 1].group !== t.group;
      if (isGroupFirst) acc[t.group] = { label: t.group, tools: [] };
      acc[t.group].tools.push(t);
      return acc;
    },
    {},
  );

  // holders：按分组汇聚「携带该组工具的智能体」。非首行的能力列展示的是整组
  // 并集——首行已代表本组，重复列示反而干扰。
  const holders = new Map<string, string[]>();
  tools.forEach((t) => {
    if (t.enabled) {
      const carriers = new Set(t.carried_by);
      carriers.forEach((id) => {
        const cur = holders.get(t.group) ?? [];
        if (!cur.includes(id)) holders.set(t.group, [...cur, id]);
      });
    }
  });

  return (
    <div className="provider-form">
      <table className="model-table">
        <thead>
          <tr>
            <th>工具</th>
            <th>能力</th>
            <th>状态</th>
            <th>当前智能体</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(groups).flatMap(([groupName, g]) =>
            g.tools.length === 0 ? (
              <tr className="group-row" key={groupName}>
                <td className="group-cell">{g.label}（0 个）</td>
                <td colSpan={4}>
                  <span className="pane-item-sub">暂无工具</span>
                </td>
              </tr>
            ) : (
              g.tools.map((t, idx) => {
                const on = t.enabled;
                const isGroupFirst = idx === 0;
                const disabledToolTip =
                  "重新启用不会自动补回 —— 需回到「智能体配置」重新勾选";

                return (
                  <Fragment key={t.id}>
                    {isGroupFirst && (
                      <tr className="group-row">
                        <td className="group-cell" colSpan={5}>
                          <strong>{g.label}</strong>（{g.tools.length} 个）
                        </td>
                      </tr>
                    )}
                    <tr
                      style={
                        isGroupFirst
                          ? undefined
                          : { borderTop: "1px solid #eee2cf" }
                      }
                    >
                      {/* 非首行的工具格保持空格（原稿在 td 内再嵌 td，
                          会触发 validateDOMNesting 警告，故压平为单 td）。 */}
                      <td
                        className={isGroupFirst ? undefined : "pane-item-sub"}
                        style={isGroupFirst ? undefined : { padding: 0 }}
                      >
                        {isGroupFirst ? (
                          <span>
                            {t.icon} {t.label}
                          </span>
                        ) : null}
                      </td>
                      {/* 整列缺 td 会让行只有 4 格：禁用首行工具时表格错位，
                          故 td 始终渲染，仅内容按分支出现。 */}
                      <td>
                        {!isGroupFirst && on ? (
                          <span className="pane-item-sub">
                            通过 agent 定义 YAML 声明，组内只列一次；此处启停整组生效。
                            {holders.get(t.group)!.map((id) => (
                              <span key={id} className="check-opt on">{id} →</span>
                            ))}
                          </span>
                        ) : isGroupFirst && (on || t.id === "bash") ? (
                          <span className="pane-item-sub">
                            {t.carried_by} →
                          </span>
                        ) : null}
                      </td>
                      {on ? (
                        <td>
                          <span className="tool-state on">● 已启用</span>
                        </td>
                      ) : (
                        <td />
                      )}
                      <td style={on ? undefined : { padding: 0 }}>
                        {on ? (
                          <>
                            {t.carried_by.length > 0 ? (
                              <span className="check-opt on">
                                {t.carried_by} →
                              </span>
                            ) : (
                              <span className="pane-item-sub">—</span>
                            )}
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td>
                        {on ? (
                          <button
                            className="btn-secondary"
                            disabled={saving}
                            onClick={() => {
                              if (
                                window.confirm(
                                  `禁用「${t.icon} ${t.label}」将同时从所有智能体移除该工具，确认禁用？`,
                                )
                              ) {
                                setTip(null);
                                onAction(() => putToolEnabled(t.id, false));
                              }
                            }}
                          >
                            禁用
                          </button>
                        ) : (
                          <button
                            className="btn-secondary"
                            disabled={saving}
                            onClick={() => {
                              setTip(disabledToolTip);
                              onAction(() => putToolEnabled(t.id, true));
                            }}
                          >
                            启用
                          </button>
                        )}
                      </td>
                    </tr>
                  </Fragment>
                );
              })
            ),
          )}
        </tbody>
      </table>
      {tip !== null && <p className="field-hint">{tip}</p>}
    </div>
  );
}
