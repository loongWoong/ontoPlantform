import { useCallback, useEffect, type RefObject } from "react";

const TOKEN_KEY = "onto.api_token";

/**
 * 智能视图 iframe 的父页面桥。
 *
 * iframe 是 sandbox 隔离的不透明源：它拿不到 console 的认证令牌，也不能直接
 * 请求平台 API（服务端会按 Origin 拒绝）。运行时启动后先发 `onto.smartui.ready`，
 * 父页面把 sessionStorage 里的令牌交给它；之后运行时把每次读数/动作请求
 * 以 `onto.smartui.request` 发过来，父页面用同源 fetch 代理，再把结果回给
 * iframe。路径被限制在当前工作空间的 `/smart-ui/` 下，避免这个代理成为
 * 绕过平台鉴权的后门。
 */
export function useSmartUiBridge(
  frameRef: RefObject<HTMLIFrameElement | null>,
  wid: string | null,
  reloadKey = 0,
) {
  const postAuthToken = useCallback(() => {
    let token = "";
    try {
      token = window.sessionStorage.getItem(TOKEN_KEY) ?? "";
    } catch {
      token = "";
    }
    frameRef.current?.contentWindow?.postMessage(
      { type: "onto.smartui.auth", token },
      "*",
    );
  }, [frameRef]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow) return;
      const data = event.data as
        | { type?: unknown; id?: unknown; method?: unknown; path?: unknown; body?: unknown }
        | null;
      if (!data) return;
      if (data.type === "onto.smartui.ready") {
        postAuthToken();
        return;
      }
      if (data.type !== "onto.smartui.request" || !wid) return;

      const id = typeof data.id === "string" ? data.id : "";
      const method = data.method === "GET" ? "GET" : data.method === "POST" ? "POST" : "";
      const path = typeof data.path === "string" ? data.path : "";
      const reply = (status: number, body: unknown) => {
        const source = event.source as Window | null;
        source?.postMessage({ type: "onto.smartui.response", id, status, body }, "*");
      };
      // Only the current workspace's smart-UI endpoints may be driven from the
      // sandbox, and only with the two verbs the runtime needs.
      if (!id || !method
          || !path.startsWith(`/api/workspaces/${encodeURIComponent(wid)}/smart-ui/`)) {
        reply(400, { error: "smart-ui bridge rejected the request path" });
        return;
      }
      let token = "";
      try {
        token = window.sessionStorage.getItem(TOKEN_KEY) ?? "";
      } catch {
        token = "";
      }
      const headers: Record<string, string> = {};
      if (token) headers.Authorization = `Bearer ${token}`;
      if (method === "POST") headers["Content-Type"] = "application/json";
      void (async () => {
        try {
          const res = await fetch(path, {
            method,
            headers,
            body: method === "POST" ? JSON.stringify(data.body ?? {}) : undefined,
          });
          const text = await res.text();
          let body: unknown = null;
          try {
            body = text ? JSON.parse(text) : null;
          } catch {
            body = text;
          }
          reply(res.status, body);
        } catch (error) {
          reply(0, { error: (error as Error).message || "console bridge fetch failed" });
        }
      })();
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [wid, reloadKey, postAuthToken, frameRef]);

  return postAuthToken;
}

export function smartUiFrameSrc(wid: string, generationId?: string): string {
  const base = `/smart-ui/?workspace=${encodeURIComponent(wid)}&api=${encodeURIComponent(window.location.origin)}`;
  return generationId ? `${base}&generation=${encodeURIComponent(generationId)}` : base;
}

export function smartUiStandaloneSrc(wid: string, generationId?: string): string {
  const base = `/smart-ui/?workspace=${encodeURIComponent(wid)}`;
  return generationId ? `${base}&generation=${encodeURIComponent(generationId)}` : base;
}
