import React, { useMemo, useState } from "react";
import {
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";

/**
 * 双标的收益对比图（累计收益率净值曲线）
 *
 * 默认双 Y 轴：左轴=标的A（策略+持有不动），右轴=标的B（策略+持有不动），共4条线。
 * 适合两个标的价格/收益量级差距较大的情况，左右分组阅读更清晰。
 * 可切换为「单轴归一化」：所有线共用一个百分比轴，更直观比较谁更强。
 *
 * props:
 *   seriesA / seriesB: { code, name, color, points: [{ time, strat, hold }] }
 *     strat / hold 为累计收益率（小数，如 0.12 表示 +12%）
 */
export default function CompareReturnChart({ seriesA, seriesB }) {
  const [dualAxis, setDualAxis] = useState(true);

  const data = useMemo(() => {
    const map = new Map();
    const put = (points, key) => {
      (points || []).forEach((p) => {
        const row = map.get(p.time) || { time: p.time };
        row[`${key}_strat`] = p.strat;
        row[`${key}_hold`] = p.hold;
        map.set(p.time, row);
      });
    };
    if (seriesA) put(seriesA.points, "a");
    if (seriesB) put(seriesB.points, "b");
    const rows = Array.from(map.values()).sort(
      (x, y) => new Date(x.time) - new Date(y.time)
    );
    // 抽样，避免点过多
    const max = 1200;
    if (rows.length <= max) return rows;
    const step = Math.ceil(rows.length / max);
    return rows.filter((_, i) => i % step === 0);
  }, [seriesA, seriesB]);

  const fmtPct = (v) =>
    typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "";

  const colorA = seriesA?.color || "#4f46e5";
  const colorB = seriesB?.color || "#f59e0b";

  return (
    <div>
      <div className="flex items-center justify-end mb-2">
        <button
          onClick={() => setDualAxis((v) => !v)}
          className="text-xs px-3 py-1 rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50"
        >
          {dualAxis ? "切换：单轴归一化" : "切换：双Y轴（左A右B）"}
        </button>
      </div>
      <ResponsiveContainer width="100%" height={440}>
        <ComposedChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
          <XAxis
            dataKey="time"
            tick={{ fontSize: 12 }}
            interval="preserveStartEnd"
            tickFormatter={(v) => (typeof v === "string" ? v.slice(0, 7) : v)}
          />
          <YAxis
            yAxisId="left"
            tick={{ fontSize: 12 }}
            tickFormatter={fmtPct}
            width={56}
          />
          {dualAxis && (
            <YAxis
              yAxisId="right"
              orientation="right"
              tick={{ fontSize: 12 }}
              tickFormatter={fmtPct}
              width={56}
            />
          )}
          <Tooltip
            content={({ active, payload, label }) => {
              if (!active || !payload || !payload.length) return null;
              return (
                <div className="bg-white p-3 border border-gray-200 rounded-lg shadow-lg text-sm">
                  <p className="font-semibold text-gray-900 mb-1">{label}</p>
                  {payload.map((p) => (
                    <p key={p.dataKey} style={{ color: p.color }}>
                      {p.name}: {fmtPct(p.value)}
                    </p>
                  ))}
                </div>
              );
            }}
          />
          <Legend />

          {/* 标的A：策略实线 + 持有虚线（左轴） */}
          {seriesA && (
            <>
              <Line
                yAxisId="left"
                type="monotone"
                dataKey="a_strat"
                stroke={colorA}
                strokeWidth={2}
                dot={false}
                connectNulls
                name={`${seriesA.name} 策略`}
              />
              <Line
                yAxisId="left"
                type="monotone"
                dataKey="a_hold"
                stroke={colorA}
                strokeWidth={1.5}
                strokeDasharray="5 4"
                dot={false}
                connectNulls
                name={`${seriesA.name} 持有`}
              />
            </>
          )}

          {/* 标的B：策略实线 + 持有虚线（双轴时右轴，单轴时左轴） */}
          {seriesB && (
            <>
              <Line
                yAxisId={dualAxis ? "right" : "left"}
                type="monotone"
                dataKey="b_strat"
                stroke={colorB}
                strokeWidth={2}
                dot={false}
                connectNulls
                name={`${seriesB.name} 策略`}
              />
              <Line
                yAxisId={dualAxis ? "right" : "left"}
                type="monotone"
                dataKey="b_hold"
                stroke={colorB}
                strokeWidth={1.5}
                strokeDasharray="5 4"
                dot={false}
                connectNulls
                name={`${seriesB.name} 持有`}
              />
            </>
          )}
        </ComposedChart>
      </ResponsiveContainer>
      <p className="text-xs text-gray-500 mt-2">
        实线=策略累计收益，虚线=持有不动累计收益。
        {dualAxis
          ? "当前为双 Y 轴：左轴对应标的A、右轴对应标的B，适合两者收益量级差距大时分别读数。"
          : "当前为单轴归一化：所有线共用一个百分比轴，直接比较谁的累计收益更高。"}
      </p>
    </div>
  );
}
