import { useEffect, useRef, useState, type RefObject } from "react";
import { ArrowSquareOut, ArrowsClockwise, Sparkle } from "@phosphor-icons/react";
import { api, type SmartUiMeta } from "../api";
import { usePlatform } from "../store";
import { smartUiFrameSrc, smartUiStandaloneSrc, useSmartUiBridge } from "../smartui-bridge";
import { Btn, Card, ErrorNote, PageHead, Skeleton, Stat, Tag, useLoad } from "../ui";

/**
 * 智能视图：把当前工作空间交给本体驱动的确定性编译器，再挂载生成出来的
 * OpenUI 运行时。后端负责「平台本体 → 引擎本体 → 程序 + 真实 payload」。
 *
 * 这条链路不调用模型；模型生成在独立的「AI视图」菜单里，两者互不影响。
 * 旧链接 `?mode=studio|history` 只做一次向后兼容跳转。
 */
export function SmartUIView() {
  const { wid, workspaceName, online, params, go } = usePlatform();
  const [reloadKey, setReloadKey] = useState(0);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const meta = useLoad(() => (wid ? api.smartUiMeta(wid) : Promise.resolve(null)), [wid]);
  const postAuthToken = useSmartUiBridge(frameRef, wid, reloadKey);
  const frameSrc = wid ? smartUiFrameSrc(wid) : "";
  const standaloneSrc = wid ? smartUiStandaloneSrc(wid) : "";

  const legacyMode = params.mode;
  useEffect(() => {
    if (legacyMode !== "studio" && legacyMode !== "history") return;
    const extra: Record<string, string> = params.generation ? { generation: params.generation } : {};
    go("aiview", legacyMode === "history" ? { tab: "history", ...extra } : extra);
  }, [legacyMode, params.generation, go]);

  return (
    <>
      <PageHead
        kicker="Ontology-driven smart UI"
        title="智能视图"
        desc="同一个工作空间的本体、数据与能力，编译成一套可交互的视图；不是静态截图，也不是另起一份样例数据。此页不调用模型，模型生成在独立的「AI视图」中。"
        actions={
          <div className="btn-row">
            <Btn variant="subtle" onClick={() => meta.reload()} busy={meta.loading}>
              <ArrowsClockwise size={14} /> 刷新元数据
            </Btn>
            {wid && (
              <Btn variant="subtle" onClick={() => setReloadKey(k => k + 1)}>
                <ArrowsClockwise size={14} /> 重新加载视图
              </Btn>
            )}
            {wid && (
              <Btn
                variant="subtle"
                onClick={() => window.open(standaloneSrc, "_blank", "noopener,noreferrer")}
              >
                <ArrowSquareOut size={14} /> 新窗口打开
              </Btn>
            )}
          </div>
        }
        meta={
          <>
            <Tag kind={online ? "ok" : "warn"}>{online ? "平台在线" : "服务未连接"}</Tag>
            {wid && <Tag>{workspaceName || wid}</Tag>}
            {meta.data && <Tag kind="plain">引擎 {meta.data.bridge.engineVersion}</Tag>}
          </>
        }
      />

      {!wid ? (
        <Card tone="amber">
          <p className="card-lead">先选择一个工作空间。</p>
          <p className="card-sub card-lead-sub">
            智能视图需要已发布的本体；没有工作空间就没有可编译的业务世界。
          </p>
        </Card>
      ) : meta.error ? (
        <ErrorNote
          text={`智能视图接口不可用：${meta.error}`}
          onRetry={() => void meta.reload()}
        />
      ) : meta.loading && !meta.data ? (
        <Skeleton rows={6} />
      ) : meta.data ? (
        <RuntimePanel
          wid={wid}
          meta={meta.data}
          metaLoading={meta.loading}
          onReloadMeta={() => void meta.reload()}
          frameRef={frameRef}
          frameSrc={frameSrc}
          reloadKey={reloadKey}
          postAuthToken={postAuthToken}
        />
      ) : null}
    </>
  );
}

interface RuntimePanelProps {
  wid: string;
  meta: SmartUiMeta;
  metaLoading: boolean;
  onReloadMeta: () => void;
  frameRef: RefObject<HTMLIFrameElement | null>;
  frameSrc: string;
  reloadKey: number;
  postAuthToken: () => void;
}

function RuntimePanel({
  wid, meta, metaLoading, onReloadMeta, frameRef, frameSrc, reloadKey, postAuthToken,
}: RuntimePanelProps) {
  const counts = meta.counts ?? {};
  const totalObjects = Object.values(counts).reduce((sum, n) => sum + n, 0);

  if (meta.ready === false) {
    return (
      <Card tone="amber">
        <p className="card-lead">这个工作空间还不能生成智能视图。</p>
        <p className="card-sub card-lead-sub">{meta.reason}</p>
        {meta.guidance?.length ? (
          <ol className="smartui-flow">
            {meta.guidance.map((item) => (
              <li key={item}><b>下一步</b><span>{item}</span></li>
            ))}
          </ol>
        ) : null}
        <div className="btn-row">
          <Btn variant="subtle" onClick={onReloadMeta} busy={metaLoading}>
            <ArrowsClockwise size={14} /> 重新检查
          </Btn>
        </div>
      </Card>
    );
  }

  return (
    <>
      <div className="stat-row smartui-stats">
        <Stat label="焦点对象" value={meta.bridge.focusLabel || meta.bridge.focusObject || "—"} foot="L3 规划入口" />
        <Stat label="对象实例" value={totalObjects} foot={`${meta.objectTypes.length} 个对象类型`} />
        <Stat label="可执行动作" value={meta.actions.length} foot={`共 ${meta.capabilities.length} 个数据能力`} />
        <Stat label="默认问题" value={meta.defaultQuestion} foot="可编译为新的 program hash" />
      </div>

      <Card
        title="生成的业务视图"
        sub="L5 渲染器运行在 sandbox 隔离的不透明源 iframe 中；令牌与数据请求经 postMessage 交给父页面同源代理，查询与动作都回到当前平台的真实数据。"
        aside={
          <Tag kind="ok"><Sparkle size={12} /> 确定性编译 · 无 LLM</Tag>
        }
        tight
      >
        <iframe
          key={`${wid}-${reloadKey}`}
          ref={frameRef}
          className="smartui-frame"
          title="本体驱动智能视图"
          src={frameSrc}
          loading="eager"
          onLoad={postAuthToken}
          sandbox="allow-scripts allow-forms allow-popups"
        />
      </Card>

      <Card title="这条链路怎么走" sub="页面只负责挂载，类型与数据契约在后端统一适配。">
        <ol className="smartui-flow">
          <li><b>L0 本体</b><span>平台已发布的 {meta.objectTypes.length} 个对象类型</span></li>
          <li><b>L1 能力</b><span>由对象身份、关系、时序与数值属性派生</span></li>
          <li><b>L2 模板</b><span>画像 / 关系 / 时间线 / 统计 / 状态 / 解释 / 动作</span></li>
          <li><b>L3 规划</b><span>按问题与角色裁剪视角，失败即报错而非编造</span></li>
          <li><b>L4 编译</b><span>生成确定性 OpenUI Lang 程序与工具闭包</span></li>
          <li><b>L5 渲染</b><span>隔离 iframe 中绑定真实平台 payload，经父页面 postMessage 代理回连平台</span></li>
        </ol>
        <p className="card-sub card-lead-sub">
          说明：焦点对象、身份字段、关系、时间线、统计与状态全部由当前工作空间的
          本体与实时实例推导；没有对应字段或实例时明确留空，不用车牌、站点等固定样例补位。
        </p>
      </Card>
    </>
  );
}
