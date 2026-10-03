import type { ReactNode } from "react";

interface Props {
  icon: string;
  title: string;
  desc: ReactNode;
  actions?: ReactNode;
}

/**
 * 「这一屏只有状态可看」时的状态卡（加载失败 / 后端未就绪 / 还没有数据）。
 * 原型对这类状态只有一行 .p-empty 灰字，读着像脚注，还把「唯一该做的动作」一起降级成小灰按钮；
 * 这里把形态收口：卡片照 .ctx-card，竖排居中与大图标照 .kb-empty，主/次按钮照 .btn-primary / .mini-btn。
 * 外层给 .state-page 时连上下也居中（整页只有这张卡），否则只水平居中（标题栏下方）。
 */
export default function PageState({ icon, title, desc, actions }: Props) {
  return (
    <div className="page-state">
      <span className="big">{icon}</span>
      <h2>{title}</h2>
      <p>{desc}</p>
      {actions && <div className="acts">{actions}</div>}
    </div>
  );
}
