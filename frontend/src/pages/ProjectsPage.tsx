import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  deleteProject,
  getCapabilities,
  getProjects,
  type Project,
} from "../api/client";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [toastMsg, setToastMsg] = useState("");
  const toastTimer = useRef<number>();

  const toast = useCallback((msg: string) => {
    setToastMsg(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(""), 2200);
  }, []);

  const reload = useCallback(async () => {
    try {
      const [pj, caps] = await Promise.all([getProjects(), getCapabilities()]);
      setProjects(pj.projects);
      setAgentNames(Object.fromEntries(caps.agents.map((a) => [a.id, a.name])));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);

  async function onRemove(p: Project) {
    // 与 /kb 页同款原生确认（CDP 自动化会挂，人工点击无碍）
    if (!window.confirm(`删除项目「${p.name}」？删除后不可恢复。`)) return;
    try {
      await deleteProject(p.id);
      toast(`已删除项目「${p.name}」`);
      void reload();
    } catch (err) {
      toast(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>项目管理</h1>
        <span className="num">共 {projects.length} 个</span>
        <div className="spacer" />
        <button className="btn-primary" onClick={() => undefined} disabled>
          ＋ 新建项目
        </button>
      </div>
      {error && <p className="p-empty">加载失败：{error}</p>}
      {!error && projects.length === 0 && (
        <p className="p-empty">还没有项目，点右上「＋ 新建项目」创建第一个。</p>
      )}
      {projects.length > 0 && (
        <div className="p-table">
          <table>
            <thead>
              <tr>
                <th style={{ width: 150 }}>项目名称</th>
                <th>描述</th>
                <th style={{ width: 190 }}>本地文件目录</th>
                <th style={{ width: 200 }}>启用智能体</th>
                <th style={{ width: 90 }}>知识库</th>
                <th style={{ width: 60 }}>会话数</th>
                <th style={{ width: 130 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr key={p.id}>
                  <td><span className="p-name">{p.name}</span></td>
                  <td className="p-desc">{p.desc || <span className="p-none">暂无描述</span>}</td>
                  <td><span className="p-dir" title={p.dir}>{p.dir}</span></td>
                  <td>{p.agents.map((a) => (
                    <span key={a} className="a-badge">{agentNames[a] || a}</span>
                  ))}</td>
                  {/* 知识库列只显别名：真实知识库 id 与实体路径不外泄（脱敏裁定） */}
                  <td><span className="p-kb">{p.kb}</span></td>
                  <td>{p.session_count}</td>
                  <td><div className="row-acts">
                    <button className="mini-btn" onClick={() => undefined} disabled>编辑</button>
                    <button className="mini-btn danger" onClick={() => void onRemove(p)}>删除</button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="p-tip">
        提示：项目与智能体是多对多关系——此处勾选的智能体即聊天页「当前智能体」的可选范围；
        本地文件目录与知识库配置在项目创建后不可修改。
      </p>
      {toastMsg && <div className="toast show">{toastMsg}</div>}
    </div>
  );
}
