import React, { useState, useEffect, useCallback } from "react";
import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";
import { ArrowLeft, Waves, Info, RefreshCw } from "lucide-react";
import { runFishBasin } from "@shared/services/api";
import { useSortableData } from "@shared/hooks/useSortableData";
import SortableTh from "@shared/components/ui/SortableTh";

const pct = (v) => (typeof v === "number" ? (v * 100).toFixed(2) + "%" : "—");
const signedPct = (v) => (typeof v === "number" ? (v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%" : "—");

const STATE_STYLE = {
  bull: { bg: "from-red-50 to-rose-100", text: "text-red-700", dot: "bg-red-500", label: "牛市状态" },
  bear: { bg: "from-green-50 to-emerald-100", text: "text-green-700", dot: "bg-green-500", label: "熊市状态" },
  mixed: { bg: "from-amber-50 to-yellow-100", text: "text-amber-700", dot: "bg-amber-500", label: "分化 / 震荡" },
  neutral: { bg: "from-gray-50 to-gray-100", text: "text-gray-700", dot: "bg-gray-400", label: "未知" },
};

export default function FishBasinPage() {
  const [buffer, setBuffer] = useState(0);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async (opts = {}) => {
    setLoading(true);
    setError(null);
    try {
      const resp = await runFishBasin({ buffer, ...opts });
      setData(resp.data);
    } catch (e) {
      setError(e.message || "加载失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }, [buffer]);

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const results = data?.results || [];
  const { sorted, sortKey, sortDir, requestSort } = useSortableData(results, {
    key: "strength",
    dir: "asc",
  });

  const ms = data?.market_state;
  const style = STATE_STYLE[ms?.tone] || STATE_STYLE.neutral;

  return (
    <>
      <Helmet>
        <title>鱼盆模型 · 市场风向标</title>
      </Helmet>

      <div className="space-y-6">
        {/* 标题 */}
        <div className="bg-white rounded-xl shadow-lg p-6">
          <div className="flex items-center justify-between flex-wrap gap-4">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-sky-100 rounded-lg">
                <Waves className="w-6 h-6 text-sky-600" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-gray-900">鱼盆模型 · 市场风向标</h1>
                <p className="text-sm text-gray-600">
                  以 20 日线为临界值，判断主流指数强弱，勾勒市场"大势"
                </p>
              </div>
            </div>
            <Link to="/" className="flex items-center gap-1 text-sm text-gray-600 hover:text-sky-600">
              <ArrowLeft className="w-4 h-4" />
              返回首页
            </Link>
          </div>
          <div className="mt-4 bg-sky-50 border border-sky-200 rounded-lg p-3 flex items-start gap-2">
            <Info className="w-4 h-4 text-sky-600 mt-0.5 flex-shrink-0" />
            <p className="text-sm text-sky-800">
              鱼盆模型用于判断市场大势强弱：收盘价 ≥ 临界值为 YES（偏强），&lt; 临界值为 NO（偏弱）。
              <b>YES/NO 仅代表指数相对 20 日线的强弱，非买卖信号。</b>
              全部 YES 通常对应"牛市状态"，全部 NO 对应"熊市状态"，遵循"去弱留强"。
            </p>
          </div>
        </div>

        {/* 市场状态横幅 */}
        {ms && (
          <div className={`bg-gradient-to-r ${style.bg} rounded-xl shadow-lg p-6`}>
            <div className="flex items-center justify-between flex-wrap gap-4">
              <div className="flex items-center gap-3">
                <span className={`inline-block w-4 h-4 rounded-full ${style.dot}`} />
                <div>
                  <div className={`text-2xl font-bold ${style.text}`}>{style.label}</div>
                  <div className="text-sm text-gray-600 mt-1">
                    {ms.total > 0 ? `${ms.yes}/${ms.total} 个指数处于 YES（站上临界值）` : "暂无数据"}
                  </div>
                </div>
              </div>
              <div className="text-xs text-gray-500">
                数据时间：{data?.results?.[0]?.latest_date || "—"} · 更新于 {data?.generated_at}
                {data?.from_cache ? "（缓存）" : ""}
              </div>
            </div>
          </div>
        )}

        {/* 控制栏 */}
        <div className="bg-white rounded-xl shadow-lg p-6">
          <div className="flex items-end gap-4 flex-wrap">
            <div>
              <label className="block text-sm text-gray-600 mb-1">临界值浮动（%）</label>
              <input
                type="number"
                value={buffer}
                onChange={(e) => setBuffer(parseFloat(e.target.value) || 0)}
                step="0.5"
                min="-5"
                max="5"
                className="w-32 border border-gray-300 rounded-lg px-3 py-2"
              />
              <p className="text-xs text-gray-500 mt-1">
                0 = 纯 20 日线；填 2 表示临界值 = 20日线 × 1.02（更需站稳才算 YES）
              </p>
            </div>
            <button
              onClick={() => load()}
              disabled={loading}
              className="px-5 py-2.5 bg-sky-600 text-white rounded-lg hover:bg-sky-700 disabled:opacity-50 font-medium"
            >
              {loading ? "计算中..." : "应用并刷新"}
            </button>
            <button
              onClick={() => load({ refresh: 1 })}
              disabled={loading}
              className="flex items-center gap-1 px-4 py-2.5 border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 disabled:opacity-50"
            >
              <RefreshCw className="w-4 h-4" />
              强制重算
            </button>
          </div>
          {error && (
            <div className="mt-4 bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">{error}</div>
          )}
        </div>

        {/* 表格 */}
        <div className="bg-white rounded-xl shadow-lg p-6">
          {loading && (!data) ? (
            <div className="py-12 text-center text-gray-500">正在计算各指数临界值...</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-200 text-gray-500 text-left">
                    <SortableTh label="强度" sortKey="strength" activeKey={sortKey} dir={sortDir} onSort={requestSort} align="left" />
                    <th className="py-2 px-3">代码</th>
                    <th className="py-2 px-3">名称</th>
                    <SortableTh label="涨幅" sortKey="change_pct" activeKey={sortKey} dir={sortDir} onSort={requestSort} />
                    <SortableTh label="现价" sortKey="price" activeKey={sortKey} dir={sortDir} onSort={requestSort} />
                    <SortableTh label="临界值" sortKey="threshold" activeKey={sortKey} dir={sortDir} onSort={requestSort} />
                    <th className="py-2 px-3 text-center">状态</th>
                    <SortableTh label="状态转变时间" sortKey="status_since" activeKey={sortKey} dir={sortDir} onSort={requestSort} align="left" />
                    <SortableTh label="偏离率" sortKey="deviation" activeKey={sortKey} dir={sortDir} onSort={requestSort} />
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((r) => (
                    <tr key={r.code} className="border-b border-gray-100">
                      <td className="py-2 px-3">
                        {r.strength != null ? (
                          <span className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold ${
                            r.strength === 1 ? "bg-red-100 text-red-700" : "bg-gray-100 text-gray-600"
                          }`}>
                            {r.strength}
                          </span>
                        ) : "—"}
                      </td>
                      <td className="py-2 px-3 text-gray-500">{r.code}</td>
                      <td className="py-2 px-3 font-medium text-gray-900">{r.name}</td>
                      <td className={`py-2 px-3 text-right ${
                        typeof r.change_pct === "number" ? (r.change_pct > 0 ? "text-up-600" : r.change_pct < 0 ? "text-down-600" : "") : ""
                      }`}>{signedPct(r.change_pct)}</td>
                      <td className="py-2 px-3 text-right">{typeof r.price === "number" ? r.price.toFixed(3) : "—"}</td>
                      <td className="py-2 px-3 text-right text-gray-600">{typeof r.threshold === "number" ? r.threshold.toFixed(3) : "—"}</td>
                      <td className="py-2 px-3 text-center">
                        {r.status === "YES" && (
                          <span className="px-2 py-0.5 rounded-full bg-red-100 text-red-700 text-xs font-bold">YES</span>
                        )}
                        {r.status === "NO" && (
                          <span className="px-2 py-0.5 rounded-full bg-green-100 text-green-700 text-xs font-bold">NO</span>
                        )}
                        {!r.status && <span className="text-gray-400 text-xs">失败</span>}
                      </td>
                      <td className="py-2 px-3 text-center text-gray-500">{r.status_since || "—"}</td>
                      <td className={`py-2 px-3 text-right font-medium ${
                        typeof r.deviation === "number" ? (r.deviation > 0 ? "text-up-600" : r.deviation < 0 ? "text-down-600" : "") : ""
                      }`}>{signedPct(r.deviation)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="text-xs text-gray-500 mt-3">
                强度按偏离率绝对值排名，1 = 短期最强。临界值 = 20 日均线 × (1 + 浮动%)。
                {data?.elapsed_seconds != null && ` 本次计算耗时 ${data.elapsed_seconds}s。`}
              </p>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
