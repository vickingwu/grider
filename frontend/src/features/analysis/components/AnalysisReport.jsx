import React, { useState, useEffect, lazy, Suspense } from "react";
import { LoadingSpinner } from "@shared/components/ui";
import ReportTabs from "./ReportTabs";
import OverviewTab from "./OverviewTab";
import ErrorState from "./ErrorState";
import Disclaimer from "./Disclaimer";
import SuitabilityCard from "./ReportCards/SuitabilityCard";
import GridParametersCard from "./ReportCards/GridParametersCard";

// 懒加载回测组件
const BacktestTab = lazy(() => import("./BacktestTab"));

/**
 * 分析报告容器组件
 * 负责协调各个报告子组件和状态管理
 */
const AnalysisReport = ({
  data,
  loading,
  onBackToInput,
  onReAnalysis,
  editParamsFirst = false,
}) => {
  const [activeTab, setActiveTab] = useState(editParamsFirst ? "backtest" : "overview");
  // 从首页勾选"分析前自定义参数"进入时，自动切到回测标签（仅一次，之后尊重用户手动切换）
  const [autoTabApplied, setAutoTabApplied] = useState(false);
  useEffect(() => {
    if (editParamsFirst && !autoTabApplied) {
      setActiveTab("backtest");
      setAutoTabApplied(true);
    }
  }, [editParamsFirst, autoTabApplied]);

  // 回测标签一旦被访问就保持挂载（用 CSS 隐藏而非卸载），
  // 这样切换标签后用户编辑的参数与回测结果不会丢失。
  const [backtestMounted, setBacktestMounted] = useState(editParamsFirst);
  useEffect(() => {
    if (activeTab === "backtest") setBacktestMounted(true);
  }, [activeTab]);


  // 显示加载状态
  if (loading) {
    return (
      <LoadingSpinner
        message="正在分析标的数据..."
        showProgress={true}
        progress={75}
      />
    );
  }

  // 显示错误状态
  if (data?.error) {
    return (
      <ErrorState
        type="error"
        message={data.message}
        onBackToInput={onBackToInput}
        onReAnalysis={onReAnalysis}
      />
    );
  }

  if (!data) return null;

  const {
    etf_info,
    data_quality,
    suitability_evaluation,
    grid_strategy,
    strategy_rationale,
    adjustment_suggestions,
    input_parameters,
  } = data;

  // 数据完整性检查
  const isDataComplete = () => {
    if (!suitability_evaluation || !grid_strategy || !etf_info) {
      return false;
    }

    const dataObjects = {
      suitability_evaluation: suitability_evaluation,
      grid_strategy: grid_strategy,
      etf_info: etf_info,
    };

    const requiredFields = {
      suitability_evaluation: ["total_score", "conclusion"],
      grid_strategy: ["grid_config", "fund_allocation"],
      etf_info: ["code", "name", "current_price"],
    };

    for (const [objName, fields] of Object.entries(requiredFields)) {
      const obj = dataObjects[objName];
      for (const field of fields) {
        if (obj[field] === undefined || obj[field] === null) {
          return false;
        }
      }
    }

    return true;
  };

  if (!isDataComplete()) {
    return (
      <ErrorState
        type="data_incomplete"
        message="分析数据不完整，请重新分析"
        onBackToInput={onBackToInput}
        onReAnalysis={onReAnalysis}
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* 标签页导航 */}
      <div className="bg-white rounded-xl shadow-lg">
        <ReportTabs activeTab={activeTab} onTabChange={setActiveTab} />

        <div className="p-6">
          {/* 概览标签页 */}
          {activeTab === "overview" && (
            <OverviewTab
              etfInfo={etf_info}
              suitabilityEvaluation={suitability_evaluation}
              gridStrategy={grid_strategy}
              dataQuality={data_quality}
              inputParameters={input_parameters}
            />
          )}

          {/* 适宜度评估标签页 */}
          {activeTab === "suitability" && (
            <SuitabilityCard
              evaluation={suitability_evaluation}
              dataQuality={data_quality}
              showDetailed={true}
            />
          )}

          {/* 网格策略标签页 */}
          {activeTab === "strategy" && (
            <GridParametersCard
              gridStrategy={grid_strategy}
              inputParameters={input_parameters}
              strategyRationale={strategy_rationale}
              adjustmentSuggestions={adjustment_suggestions}
              showDetailed={true}
              dataQuality={data_quality}
              etfInfo={etf_info}
            />
          )}

          {/* 回测分析标签页：一旦挂载就保留（用 display 隐藏），避免切换标签丢失编辑参数 */}
          {backtestMounted && (
            <div style={{ display: activeTab === "backtest" ? "block" : "none" }}>
              <Suspense fallback={<div className="text-center py-12">加载中...</div>}>
                <BacktestTab
                  etfCode={etf_info.code}
                  exchangeCode={etf_info.exchange_code}
                  gridStrategy={grid_strategy}
                  type={etf_info.type}
                  totalCapital={input_parameters.total_capital}
                  autoEditParams={editParamsFirst}
                />
              </Suspense>
            </div>
          )}
        </div>
      </div>

      {/* 免责声明 */}
      <Disclaimer />
    </div>
  );
};

export default AnalysisReport;
