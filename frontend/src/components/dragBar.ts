/** 原型 :1652-1660 dragBar 的 hook 化：mousedown → document mousemove → mouseup，卸载即清理。
    KbPage 与聊天页工作区共用（第二个消费方出现才抽，行为一字不变）。 */
export function bindDragBar(el: HTMLElement, onMove: (ev: MouseEvent) => void): () => void {
  let detachMove: (() => void) | null = null;
  const down = (e: MouseEvent) => {
    e.preventDefault();
    el.classList.add("dragging");
    const move = (ev: MouseEvent) => onMove(ev);
    const up = () => {
      el.classList.remove("dragging");
      detachMove?.();
      detachMove = null;
      document.body.style.userSelect = "";
    };
    document.body.style.userSelect = "none";
    document.addEventListener("mousemove", move);
    document.addEventListener("mouseup", up);
    detachMove = () => { document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up); };
  };
  el.addEventListener("mousedown", down);
  return () => { el.removeEventListener("mousedown", down); detachMove?.(); document.body.style.userSelect = ""; };
}
