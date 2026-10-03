import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  deleteProject,
  getCapabilities,
  getProjects,
  type Project,
} from "../api/client";
import PageState from "../components/PageState";
import ProjectFormModal from "./projects/ProjectFormModal";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [agentOptions, setAgentOptions] = useState<{ id: string; name: string }[]>([]);
  const [modal, setModal] = useState<{ mode: "create" | "edit"; project: Project | null } | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [toastMsg, setToastMsg] = useState("");
  const toastTimer = useRef<number>();

  const agentNames = Object.fromEntries(agentOptions.map((a) => [a.id, a.name]));
  // 智能体选项是弹窗可用性的前提：选项为空时保存恒被「请至少选择一个智能体」拦截，入口须一并门控
  const formReady = loaded && agentOptions.length > 0;

  const toast = useCallback((msg: string) => {
    setToastMsg(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(""), 2200);
  }, []);

  const reload = useCallback(async () => {
    try {
      const [pj, caps] = await Promise.all([getProjects(), getCapabilities()]);
      setProjects(pj.projects);
      setAgentOptions(caps.agents.map((a) => ({ id: a.id, name: a.name })));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => { void reload(); }, [reload]);
  // 卸载时清 toast 定时器（同 KbPage 的收尾约定）
  useEffect(() => () => { window.clearTimeout(toastTimer.current); }, []);

  async function onRemove(p: Project) {
    // 与 /kb 页同款原生确认（CDP 自动化会挂，人工点击无碍）
    if (!window.confirm(
      `删除项目「${p.name}」？会连带删除 ${p.session_count} 条会话，删除后不可恢复。`
    )) return;
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
        {loaded && !error && <span className="num">共 {projects.length} 个</span>}
        <div className="spacer" />
        <button className="btn-primary" disabled={!formReady} onClick={() => setModal({ mode: "create", project: null })}>
          ＋ 新建项目
        </button>
      </div>
      {error && (
        <PageState
          icon="⚠️"
          title="项目列表加载失败"
          desc={error}
          actions={<button className="btn-primary" onClick={() => void reload()}>↻ 重试</button>}
        />
      )}
      {loaded && !error && projects.length === 0 && (
        <PageState
          icon="📁"
          title="还没有项目"
          desc="项目就是需求文档所在的本地目录。添加一个目录，智能体就能在那里读写文件。"
          actions={
            <button className="btn-primary" disabled={!formReady} onClick={() => setModal({ mode: "create", project: null })}>
              ＋ 新建项目
            </button>
          }
        />
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
                  <td>
                    <span className="p-dir" title={p.dir}>{p.dir}</span>
                    {!p.dir_exists && (
                      /* 提醒而不拦截：项目页照常可编辑名称/智能体，只有发送时后端才硬拦（spec 裁定 5） */
                      <span style={{ color: "var(--text-2)" }}> · 目录当前不可访问</span>
                    )}
                  </td>
                  <td>{p.agents.map((a) => (
                    <span key={a} className="a-badge">{agentNames[a] || a}</span>
                  ))}</td>
                  {/* 知识库列只显别名：真实知识库 id 与实体路径不外泄（脱敏裁定） */}
                  <td><span className="p-kb">{p.kb}</span></td>
                  <td>{p.session_count}</td>
                  <td><div className="row-acts">
                    <button className="mini-btn" disabled={!formReady} onClick={() => setModal({ mode: "edit", project: p })}>编辑</button>
                    <button className="mini-btn danger" onClick={() => void onRemove(p)}>删除</button>
                  </div></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {modal && (
        <ProjectFormModal
          mode={modal.mode}
          project={modal.project}
          agentOptions={agentOptions}
          onClose={() => setModal(null)}
          onSaved={(msg) => { toast(msg); void reload(); }}
        />
      )}
      <p className="p-tip">
        提示：此处勾选的智能体即该项目启用的智能体（聊天页的「当前智能体」只列这里启用的那些）；
        本地文件目录与知识库配置在项目创建后不可修改。
      </p>
      {toastMsg && <div className="toast show">{toastMsg}</div>}
    </div>
  );
}
