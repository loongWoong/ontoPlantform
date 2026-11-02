import { ArrowsClockwise } from "@phosphor-icons/react";
import type { AITabDefinition, AITabContext } from "../ai-view-slots";
import { AI_TAB_SLOT } from "../ai-view-slots";
import "../ai-view-slots";
import { api } from "../api";
import { usePlatform } from "../store";
import { uiSlotItems, type UISlotItem } from "../ui-slots";
import { Btn, Card, ErrorNote, PageHead, Skeleton, Tag, useLoad } from "../ui";

/**
 * AI视图：一条独立的模型生成链路。
 *
 * 用户用业务语言描述界面，模型只提议蓝图，平台负责裁决、L4 确定性编译与
 * 沙箱预览；结果可继续迭代、存草稿、发布为应用。它和「智能视图」的确定性
 * 运行链路是两个独立菜单、独立页面，互不改变对方的入口与状态。
 *
 * 标签页本身来自构建期 UI slot，不再写死在 shell 中；slot 里只有随包发布的
 * 组件，没有远程脚本。
 */
export function AIView() {
  const { wid, workspaceName, online, params, go } = usePlatform();
  const rawTabs = uiSlotItems<AITabDefinition>(AI_TAB_SLOT);
  const meta = useLoad(() => (wid ? api.smartUiMeta(wid) : Promise.resolve(null)), [wid]);
  const data = meta.data;
  const studioEnabled = data?.studio?.enabled !== false;
  // The backend UI contribution publishes the slots it can actually render.
  // A slot present in the build but absent from meta is hidden, so an old/new
  // console cannot route into a component the server does not advertise.
  const declaredSlots = data?.ui?.slots
    ?.filter((item) => item.slot === AI_TAB_SLOT)
    .sort((a, b) => a.order - b.order)
    .map((item) => item.id);
  const tabs = declaredSlots?.length
    ? rawTabs.filter((item) => declaredSlots.includes(item.id))
    : rawTabs;
  const activeTab: UISlotItem<AITabDefinition> | undefined =
    tabs.find((item) => item.id === params.tab) ?? tabs[0];
  const activeId = activeTab?.id ?? "studio";
  const generationId = params.generation || null;

  const goto = (nextTab: string, extra?: Record<string, string>) => {
    if (nextTab === "studio") {
      go("aiview", extra ?? {});
    } else {
      go("aiview", { tab: nextTab, ...extra });
    }
  };
  const openGeneration = (gid: string) => goto("studio", { generation: gid });

  return (
    <>
      <PageHead
        kicker="AI-generated smart UI"
        title="AI视图"
        desc="用业务语言描述想要的界面：模型只提议蓝图，平台负责裁决与确定性编译，结果可迭代、存草稿、发布为应用。与「智能视图」的确定性运行链路相互独立。"
        actions={
          <div className="btn-row">
            <Btn variant="subtle" onClick={() => meta.reload()} busy={meta.loading}>
              <ArrowsClockwise size={14} /> 刷新元数据
            </Btn>
          </div>
        }
        meta={
          <>
            <Tag kind={online ? "ok" : "warn"}>{online ? "平台在线" : "服务未连接"}</Tag>
            {wid && <Tag>{workspaceName || wid}</Tag>}
            {data && <Tag kind="plain">引擎 {data.bridge.engineVersion}</Tag>}
            <Tag kind="plain">独立于智能视图</Tag>
          </>
        }
      />

      {!wid ? (
        <Card tone="amber">
          <p className="card-lead">先选择一个工作空间。</p>
          <p className="card-sub card-lead-sub">
            AI视图需要已发布的本体与可执行能力；没有工作空间就没有可供模型提议的业务世界。
          </p>
        </Card>
      ) : meta.error ? (
        <ErrorNote
          text={`AI视图接口不可用：${meta.error}`}
          onRetry={() => void meta.reload()}
        />
      ) : meta.loading && !data ? (
        <Skeleton rows={6} />
      ) : data && !studioEnabled ? (
        <Card tone="amber">
          <p className="card-lead">AI视图已关闭。</p>
          <p className="card-sub card-lead-sub">
            平台开关「ONTO_SMART_UI_STUDIO=off」关闭了模型生成链路；
            「智能视图」的确定性编译与运行不受影响。
          </p>
        </Card>
      ) : data ? (
        <>
          <div className="segmented smartui-tabs" role="tablist">
            {tabs.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={activeId === item.id}
                className={activeId === item.id ? "is-active" : ""}
                title={item.data.hint}
                onClick={() => goto(item.id)}
              >
                {item.data.label}
                <span className="segmented-hint">{item.data.hint}</span>
              </button>
            ))}
          </div>

          {activeTab?.data.render({
            wid,
            meta: data,
            generationId,
            onSelect: openGeneration,
            onOpenHistory: () => goto("history"),
          } satisfies AITabContext)}
        </>
      ) : null}
    </>
  );
}
