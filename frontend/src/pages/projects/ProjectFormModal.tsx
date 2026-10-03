import { useEffect, useState } from "react";
import {
  ApiError,
  postPickDir,
  postProject,
  putProject,
  type Project,
  type ProjectFormValues,
} from "../../api/client";

const ABS_PATH = /^([A-Za-z]:[\\/]|\/|\\\\|~[\\/])/;

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
  const [picking, setPicking] = useState(false);

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

  /* 浏览…＝让同机后端弹系统的「选择文件夹」窗（postPickDir 的注释里写了为什么浏览器自己
     弹不出带路径的窗）。picking 守卫：窗还开着时再点一次，后端会叠出第二个模态窗。 */
  async function pickDir() {
    if (picking) return;
    setPicking(true);
    setTip("");
    try {
      const seed = ABS_PATH.test(dir.trim()) ? dir.trim() : "";
      const res = await postPickDir(seed);
      if (!res.path) { setTip("未选择目录，可直接粘贴绝对路径"); return; }
      setDir(res.path);
      setTip("已填入所选目录，可直接编辑修改");
    } catch (err) {
      setTip(err instanceof ApiError ? err.message : String(err));
    } finally {
      setPicking(false);
    }
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
          <button className="icon-btn" title="关闭" onClick={requestClose} disabled={saving}>✕</button>
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
                <button className="mini-btn" disabled={picking} onClick={() => void pickDir()}>
                  {picking ? "等待选择…" : "📁 浏览…"}
                </button>
              )}
            </div>
            <div className="hint">
              {editing
                ? "本地文件目录创建后不可修改。"
                : "可直接粘贴绝对路径；或点「📁 浏览…」弹出本机的「选择文件夹」窗，选完自动把真实绝对路径填进来（路径只能由本机进程给出，网页自己读不到）。产出（用例 / 脚本 / 报告）都写入该目录，创建后不可修改。"}
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
