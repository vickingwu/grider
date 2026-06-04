import React from "react";
import { Helmet } from "react-helmet-async";
import { Link } from "react-router-dom";
import { Filter } from "lucide-react";
import ParameterForm from "@features/analysis/components/ParameterForm";
import AnalysisHistory from "@features/history/components/AnalysisHistory";
import { generateAnalysisURL } from "@shared/utils/url";

/**
 * 首页组件
 * 负责展示首页内容和处理分析请求
 * 已移除顶部介绍区(HeroSection)，直接显示策略参数设置
 */
export default function HomePage() {
  // 处理分析请求 - 跳转到分析页面
  const handleAnalysis = async (parameters) => {
    const analysisUrl = generateAnalysisURL(parameters.etfCode, parameters);
    window.location.href = analysisUrl;
  };

  return (
    <>
      <Helmet>
        <title>网格交易策略分析工具</title>
        <meta
          name="description"
          content="专业的网格交易策略分析系统，基于ATR算法动态计算最优网格参数，提供详细的收益预测和风险评估。"
        />
      </Helmet>

      <div className="space-y-8">
        {/* 标的筛选器入口 */}
        <div className="flex justify-end">
          <Link
            to="/screener"
            className="flex items-center gap-2 px-4 py-2 bg-white border border-blue-200 text-blue-600 rounded-lg shadow-sm hover:bg-blue-50 transition-colors text-sm font-medium"
          >
            <Filter className="w-4 h-4" />
            网格标的筛选器
          </Link>
        </div>

        <ParameterForm onAnalysis={handleAnalysis} />

        <AnalysisHistory />
      </div>
    </>
  );
}
