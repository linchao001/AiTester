export function fmtK(n: number): string {
  if (n === 0) return "0";
  if (n % 1048576 === 0) return `${n / 1048576}M`;
  if (n % 1000000 === 0) return `${n / 1000000}M`;
  if (n % 1000 === 0) return `${n / 1000}K`;
  if (n % 1024 === 0) return `${n / 1024}K`;
  return String(n);
}

export const NOT_OPEN = "正式后端尚未开放该能力，待后续专项接入";
