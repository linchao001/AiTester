import { useState } from "react";
import {
  putApiKey,
  putDefault,
  putModelEnabled,
  type ModelsResponse,
  type ProviderInfo,
} from "../../api/client";
import { fmtK, NOT_OPEN } from "../../utils";

interface ModelPaneProps {
  models: ModelsResponse;
  saving: boolean;
  onAction: (action: () => Promise<ModelsResponse>) => void;
}

export default function ModelPane({ models, saving, onAction }: ModelPaneProps) {
  const [selectedId, setSelectedId] = useState(models.providers[0].id);
  const [keyInput, setKeyInput] = useState("");
  const [keyVisible, setKeyVisible] = useState(false);
  const [tip, setTip] = useState("");
  const provider =
    models.providers.find((p) => p.id === selectedId) ?? models.providers[0];
  const lock = !!provider.freeze_url || (provider.base_options?.length ?? 0) > 0;

  function pick(p: ProviderInfo): void {
    setSelectedId(p.id);
    setKeyInput("");
    setKeyVisible(false);
    setTip("");
  }

  return (
    <>
      <div className="set-left">
        <div className="set-l-head">
          模型提供商 <span className="num">{models.providers.length}</span>
        </div>
        <div className="m-list">
          {models.providers.map((p) => (
            <div
              key={p.id}
              className={p.id === provider.id ? "m-item on" : "m-item"}
              onClick={() => pick(p)}
            >
              <div className="n">
                <span className="nm">{p.name}</span>
                {models.default_uid.startsWith(`${p.id}/`) && <span className="d-tag">默认</span>}
              </div>
              <div className="s">
                <span className="p-tag">内置</span>
                {p.models.length} 模型 · {p.has_key ? "已配置" : "未配 Key"}
              </div>
            </div>
          ))}
        </div>
        <button className="mini-btn add" disabled title={NOT_OPEN}>＋ 添加提供商</button>
      </div>
      <div className="set-right">
        <div className="sec-title">
          提供商配置{" "}
          <span>
            <span className="p-tag">内置</span>
            <span className="m-sub mono">{provider.id}</span>
          </span>
        </div>
        <div className="grid2">
          <div className="field">
            <label>显示名 <span className="req">*</span></label>
            <input type="text" maxLength={24} value={provider.name} disabled />
          </div>
          <div className="field">
            <label>协议</label>
            <select value={provider.proto ?? "openai"} disabled>
              <option value="openai">OpenAI Chat 兼容</option>
              <option value="openai-response">OpenAI Response</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </div>
        </div>
        <div className="field">
          <label>Base URL <span className="req">*</span></label>
          <div className="ro">
            <input type="text" style={{ flex: 1 }} value={provider.base_url} disabled />
            {provider.base_options && provider.base_options.length > 0 && (
              <select style={{ width: 150 }} value={provider.base_url} disabled>
                {provider.base_options.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            )}
            <span className="lock" title="内置提供商地址已冻结" hidden={!lock}>
              🔒
            </span>
          </div>
        </div>
        <div className="field">
          <label>API Key</label>
          <div className="ro">
            <input
              type={keyVisible ? "text" : "password"}
              style={{ flex: 1 }}
              autoComplete="off"
              spellCheck={false}
              value={keyInput}
              placeholder={
                provider.has_key
                  ? `当前 ${provider.key_masked}（已配置，留空则不变）`
                  : provider.key_prefix
                    ? `以 ${provider.key_prefix} 开头`
                    : "未配置"
              }
              onChange={(e) => setKeyInput(e.target.value)}
            />
            <label className="eye">
              <input type="checkbox" checked={keyVisible} onChange={(e) => setKeyVisible(e.target.checked)} />
              显示
            </label>
            <label className="eye" title={NOT_OPEN}>
              <input type="checkbox" disabled />
              免 Key
            </label>
          </div>
          <div className="hint">
            {provider.has_key
              ? "已配置（掩码显示）。勾选「显示」查看明文。"
              : "未配置 API Key —— 该提供商下的模型在聊天页不可选择。"}
          </div>
        </div>
        <div className="set-acts">
          <button className="mini-btn" disabled title={NOT_OPEN}>🔌 测试连接</button>
          <span className="test-state"></span>
          <div className="spacer"></div>
          <button
            className="mini-btn"
            disabled={keyInput === "" || saving}
            onClick={() =>
              onAction(async () => {
                const resp = await putApiKey(provider.id, keyInput);
                setKeyInput("");
                setTip("已保存 API Key");
                return resp;
              })
            }
          >
            保存配置
          </button>
          {provider.has_key && (
            <button
              className="mini-btn danger"
              disabled={saving}
              onClick={() =>
                onAction(async () => {
                  setTip("已清除 API Key");
                  return putApiKey(provider.id, "");
                })
              }
            >
              清除 Key
            </button>
          )}
        </div>
        <div className="set-tip" style={tip.startsWith("已") ? { color: "var(--ok)" } : undefined}>{tip}</div>

        <div className="sec-title" style={{ marginTop: 6 }}>
          模型 <span className="num">{provider.models.length}</span>{" "}
          <span className="m-sub">数值默认取自官方文档，可按需修改</span>
        </div>
        <div className="mdl-wrap">
          <table className="mdl-table">
            <thead>
              <tr>
                <th>模型 ID</th>
                <th>显示名</th>
                <th style={{ width: 92 }}>最大输出</th>
                <th style={{ width: 104 }}>最大上下文</th>
                <th style={{ width: 120 }}>能力</th>
                <th style={{ width: 52 }}>启用</th>
                <th style={{ width: 96 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {provider.models.length === 0 ? (
                <tr>
                  <td colSpan={7} className="mdl-empty">
                    该提供商暂无模型 —— 点「＋ 添加自定义模型」或「↻ 发现模型」
                  </td>
                </tr>
              ) : (
                provider.models.map((m) => {
                  const uid = `${provider.id}/${m.id}`;
                  return (
                    <tr key={m.id} className={uid === models.default_uid ? "on" : undefined}>
                      <td>
                        <div className="mid">{m.id}</div>
                        {m.note !== undefined && <div className="mnote">{m.note}</div>}
                      </td>
                      <td>
                        {m.name ?? m.id}
                        {m.recommended && <span className="d-tag">推荐</span>}
                      </td>
                      <td className="num-c">
                        {m.max_output}
                        <span className="k">{fmtK(m.max_output)}</span>
                      </td>
                      <td className="num-c">
                        {m.context}
                        <span className="k">{fmtK(m.context)}</span>
                      </td>
                      <td>
                        <span className="cap-tag">
                          {m.caps && m.caps.length > 0 ? m.caps.join(" · ") : "—"}
                        </span>
                      </td>
                      <td className="num-c">
                        <input
                          type="checkbox"
                          checked={m.enabled}
                          disabled={saving}
                          title="启用后可在聊天页选择"
                          onChange={(e) =>
                            onAction(() => putModelEnabled(provider.id, m.id, e.target.checked))
                          }
                        />
                      </td>
                      <td>
                        <div className="row-acts">
                          <button className="mini-btn" disabled title={NOT_OPEN}>编辑</button>
                          {uid !== models.default_uid && (
                            <button
                              className="mini-btn"
                              disabled={saving}
                              onClick={() =>
                                onAction(async () => {
                                  setTip(`已将「${m.name ?? m.id}」设为全局默认`);
                                  return putDefault(uid);
                                })
                              }
                            >
                              默认
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        <div className="set-acts" style={{ marginTop: 8 }}>
          <button className="mini-btn add" disabled title={NOT_OPEN}>＋ 添加自定义模型</button>
          <button className="mini-btn" disabled title={NOT_OPEN}>↻ 发现模型</button>
        </div>
      </div>
    </>
  );
}
