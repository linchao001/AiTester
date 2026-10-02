import { useEffect, useState } from "react";
import {
  ApiError,
  postProject,
  putProject,
  type Project,
  type ProjectFormValues,
} from "../../api/client";

declare global {
  interface Window {
    showDirectoryPicker?: (opts?: { mode?: "read" | "readwrite" }) => Promise<{ name: string }>;
  }
}

const ABS_PATH = /^([A-Za-z]:[\\/]|\/|\\\\|~[\\/])/;
const PROJECT_ROOT_DEFAULT = "D:/work/projects";
const joinPath = (parent: string, name: string) => `${parent.replace(/[\\/]+$/, "")}/${name}`;
// 降级只作用于本文件：NotAllowedError 等瞬时错误不代表浏览器永久没有该能力，写坏 window 会殃及同标签页一切消费方且只有刷新能恢复
let pickerDegraded = false;

interface ProjectFormModalProps {
  mode: "create" | "edit";
  project: Project | null;
  agentOptions: { id: string; name: string }[];
  onClose: () => void;
  onSaved: (msg: string) => void;
}

export default function ProjectFormModal({
  mode, project, agentOptions, onClose, onSaved,
}: ProjectFormModalProps) {
  const editing = mode === "edit";
  const [name, setName] = useState(project?.name ?? "");
  const [desc, setDesc] = useState(project?.desc ?? "");
  const [dir, setDir] = useState(project?.dir ?? "");
  const [agents, setAgents] = useState<string[]>(
    project?.agents ?? (agentOptions[0] ? [agentOptions[0].id] : [])
  );
  const [tip, setTip] = useState("");
  const [saving, setSaving] = useState(false);

  /* 统一关闭入口：saving 期间任何路径都不得卸载弹窗——请求结果会落在已卸载组件上，用户零反馈误以为成功 */
  function requestClose() {
    if (saving) return;
    onClose();
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) { if (e.key === "Escape") requestClose(); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, saving]);

  function toggle(id: string) {
    setAgents((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  /* 浏览…：浏览器读不到所选文件夹的完整路径，父目录需确认后才能拼成绝对路径（原型同款） */
  async function pickDir() {
    const raw = dir.trim();
    let picked = "";
    if (!pickerDegraded && typeof window.showDirectoryPicker === "function") {
      try {
        picked = (await window.showDirectoryPicker({ mode: "readwrite" })).name;
      } catch (err) {
        if ((err as { name?: string })?.name === "AbortError") return;
        // 本次浏览器/上下文不可用：降级走手输回落，不让按钮再撞同一次
        pickerDegraded = true;
      }
    }
    if (!picked) {
      const typed = window.prompt("输入本地目录的绝对路径", ABS_PATH.test(raw) ? raw : PROJECT_ROOT_DEFAULT);
      if (typed === null) return;
      setDir(typed.trim());
      setTip(typed.trim() && !ABS_PATH.test(typed.trim()) ? "该路径不是绝对路径，请补全盘符或根路径" : "");
      return;
    }
    if (ABS_PATH.test(raw)) {
      setDir(joinPath(raw, picked));
      setTip(`已补全子目录：${picked}`);
      return;
    }
    const parent = window.prompt(
      `已取到文件夹名「${picked}」，浏览器读不到它的完整路径，请确认绝对父目录`,
      PROJECT_ROOT_DEFAULT
    );
    if (parent === null) { setDir(picked); setTip("请在该文件夹名前补全绝对父路径"); return; }
    const base = ABS_PATH.test(parent.trim()) ? parent.trim() : PROJECT_ROOT_DEFAULT;
    setDir(joinPath(base, picked));
    setTip("已拼出绝对路径，前缀可直接编辑修改");
  }

  /* 校验顺序与文案逐条对齐原型 btnProjSave；服务端 400 的 detail 落在同一个 tip 位 */
  function validate(): string {
    const n = name.trim();
    if (!n) return "请填写项目名称";
    if (!dir.trim()) return "请填写本地文件目录";
    if (!ABS_PATH.test(dir.trim())) return "目录必须是绝对路径，例如 D:/work/projects/order-system";
    if (!agents.length) return "请至少选择一个智能体";
    return "";
  }

  async function save() {
    const first = validate();
    if (first) { setTip(first); return; }
    const values: ProjectFormValues = {
      name: name.trim(), desc: desc.trim(), dir: dir.trim(), agents,
    };
    setSaving(true);
    try {
      if (editing && project) {
        await putProject(project.id, values);
        onSaved(`已保存项目「${values.name}」`);
      } else {
        await postProject(values);
        onSaved(`已创建项目「${values.name}」`);
      }
    } catch (err) {
      // 同名、不可改字段、平台智能体等由服务端裁定，detail 直接呈现
      setTip(err instanceof ApiError ? err.message : String(err));
      setSaving(false);
      return;
    }
    setSaving(false);
    onClose();
  }

  return (
    <div className="mask" onMouseDown={(e) => { if (e.target === e.currentTarget) requestClose(); }}>
      <div className="modal" role="dialog" aria-modal="true">
        <div className="m-head"><span>{editing ? "编辑项目" : "新建项目"}</span>
          <div className="spacer" />
          <button className="icon-btn" title="关闭" onClick={requestClose}>✕</button>
        </div>
        <div className="m-body">
          <div className="field">
            <label htmlFor="pfName">项目名称 <span className="req">*</span></label>
            <input id="pfName" type="text" maxLength={30} value={name}
              placeholder="例如：订单系统"
              onChange={(e) => { setName(e.target.value); setTip(""); }} />
          </div>
          <div className="field">
            <label htmlFor="pfDesc">项目描述</label>
            <textarea id="pfDesc" maxLength={200} value={desc}
              placeholder="一句话说明这个项目的测试范围"
              onChange={(e) => { setDesc(e.target.value); setTip(""); }} />
          </div>
          <div className="field">
            <label htmlFor="pfDir">本地文件目录（绝对路径） <span className="req">*</span></label>
            <div className="ro">
              <input id="pfDir" type="text" spellCheck={false} value={dir} disabled={editing}
                placeholder="例如 D:/work/projects/order-system 或 /home/me/order-system"
                onChange={(e) => { setDir(e.target.value); setTip(""); }} />
              {!editing && (
                <button className="mini-btn" onClick={() => void pickDir()}>📁 浏览…</button>
              )}
            </div>
            <div className="hint">
              {editing
                ? "本地文件目录创建后不可修改。"
                : "可直接粘贴绝对路径；或点「浏览…」选中文件夹，再确认它的绝对父目录，自动拼成完整路径（浏览器读不到所选文件夹的完整路径）。产出（用例 / 脚本 / 报告）都写入该目录，创建后不可修改。"}
            </div>
          </div>
          <div className="field">
            <label>启用智能体 <span className="req">*</span>（可多选）</label>
            <div className="agent-opts">
              {agentOptions.map((a) => (
                <label key={a.id} className={agents.includes(a.id) ? "on" : ""}>
                  <input type="checkbox" checked={agents.includes(a.id)}
                    onChange={() => { toggle(a.id); setTip(""); }} />
                  {a.name}
                </label>
              ))}
            </div>
            <div className="hint">至少选择一个智能体。</div>
          </div>
          {/* 知识库：默认且不可改，只显示别名（脱敏裁定） */}
          <div className="field">
            <label htmlFor="pfKb">知识库 <span className="req">*</span>（默认且不可修改）</label>
            <div className="ro">
              <input id="pfKb" type="text" value={project?.kb ?? "kb"} readOnly />
            </div>
            <div className="hint">知识库配置默认且不可修改，所有项目共用同一份知识库。</div>
          </div>
        </div>
        <div className="m-foot">
          <span className="m-tip">{tip}</span>
          <div className="spacer" />
          <button className="mini-btn" onClick={requestClose} disabled={saving}>取消</button>
          <button className="btn-primary" onClick={() => void save()} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}
