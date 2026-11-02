import { ArrowsClockwise } from "@phosphor-icons/react";
import { api } from "../api";
import { usePlatform } from "../store";
import { Btn, Card, ErrorNote, PageHead, Skeleton, Tag, useLoad } from "../ui";
import { SmartUIGenerationHistory, SmartUIStudio } from "./SmartUIStudio";

type AITab = "studio" | "history";

const TABS: { id: AITab; label: string; hint: string }[] = [
  { id: "studio", label: "AI 生成台", hint: "模型提议 · 平台编译" },
  { id: "history", label: "生成记录", hint: "版本、裁决与指纹" },
];

/**
 * AI视图：一条独立的模型生成链路。
 *
 * 用户用业务语言描述界面，模型只提议蓝图，平台负责裁决、L4 确定性编译与
 * 沙箱预览；结果可继续迭代、存草稿、发布为应用。它和「智能视图」的确定性
 * 运行链路是两个独立菜单、独立页面，互不改变对方的入口与状态。
 */
export function AIView() {
  const { wid, workspaceName, online, params, go } = usePlatform();
  const tab: AITab = params.tab === "history" ? "history" : "studio";
  const generationId = params.generation || null;
  const meta = useLoad(() => (wid ? api.smartUiMeta(wid) : Promise.resolve(null)), [wid]);
  const data = meta.data;
  const studioEnabled = data?.studio?.enabled !== false;

  const goto = (nextTab: AITab, extra?: Record<string, string>) => {
    if (nextTab === "history") {
      go("aiview", { tab: "history", ...extra });
    } else {
      go("aiview", extra ?? {});
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
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={tab === item.id}
                className={tab === item.id ? "is-active" : ""}
                title={item.hint}
                onClick={() => goto(item.id)}
              >
                {item.label}
                <span className="segmented-hint">{item.hint}</span>
              </button>
            ))}
          </div>

          {tab === "history" ? (
            <SmartUIGenerationHistory wid={wid} onOpen={openGeneration} />
          ) : (
            <SmartUIStudio
              wid={wid}
              meta={data}
              generationId={generationId}
              onSelect={openGeneration}
              onOpenHistory={() => goto("history")}
            />
          )}
        </>
      ) : null}
    </>
  );
}
