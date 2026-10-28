/** 统一状态徽标：颜色只由 status.ts 决定，组件里不写“成功”文案。 */
import { Tag } from "../ui";
import { statusMeta } from "../lib/status";

export function StatusBadge({ status, label, hint }: {
  status: string | null | undefined;
  label?: string;
  hint?: string;
}) {
  const meta = statusMeta(status);
  return (
    <span className="status-badge" title={hint ?? meta.hint ?? meta.label}>
      <Tag kind={meta.tone}>{label ?? meta.label}</Tag>
    </span>
  );
}
