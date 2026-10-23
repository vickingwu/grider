import React, { useEffect, useState } from "react";
import { Loader2, AlertTriangle } from "lucide-react";
import { engineEvents, ensureEngine, getEngineState } from "@shared/services/engine/pyodideClient";
import { RETRY_LABEL, errorText, loadingText } from "./engineGateCopy";

/**
 * 在线引擎状态横幅（仅 Pyodide 构建通过 React.lazy 挂载）。
 * 非阻塞：页面照常渲染，API 调用在引擎就绪前自动等待。
 * 根元素带 data-engine-status="loading|ready|error" 供 E2E 断言；
 * 就绪态不可见，只保留带该属性的隐藏 span。
 */
export default function EngineGate() {
  const [state, setState] = useState(getEngineState);

  useEffect(() => {
    const onChange = (event) => setState(event.detail);
    engineEvents.addEventListener("statuschange", onChange);
    ensureEngine().catch(() => {}); // 失败态由状态事件呈现，这里不再抛出
    return () => engineEvents.removeEventListener("statuschange", onChange);
  }, []);

  const handleRetry = () => window.location.reload();

  if (state.status === "error") {
    return (
      <div
        data-engine-status="error"
        role="alert"
        className="mb-4 flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-600" aria-hidden="true" />
        <p className="flex-1 break-all">{errorText(state)}</p>
        <button
          type="button"
          onClick={handleRetry}
          className="flex-shrink-0 rounded-md bg-red-600 px-3 py-1 text-xs font-medium text-white hover:bg-red-700"
        >
          {RETRY_LABEL}
        </button>
      </div>
    );
  }

  if (state.status === "ready") {
    return <span data-engine-status="ready" hidden />;
  }

  return (
    <div
      data-engine-status="loading"
      role="status"
      aria-live="polite"
      className="mb-4 flex items-center gap-2 rounded-lg border border-sky-200 bg-sky-50 p-3 text-sm text-sky-800"
    >
      <Loader2 className="h-4 w-4 flex-shrink-0 animate-spin text-sky-600" aria-hidden="true" />
      <span>{loadingText(state)}</span>
    </div>
  );
}
