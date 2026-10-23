/**
 * EngineGate 文案（在线版专用）。
 */
import { INIT_STAGES } from '@shared/services/engine/engineConfig';

/** 阶段分母（n/5） */
export const STAGE_TOTAL = INIT_STAGES.length;

/** 重试按钮文字 */
export const RETRY_LABEL = '重试';

/**
 * 加载态文案。
 * @param {{stage: string|null, transferBytes: number, elapsedMs: number}} s
 * @returns {string}
 */
export const loadingText = (s) => {
  const index = Math.max(INIT_STAGES.indexOf(s.stage), 0) + 1;
  const mb = (s.transferBytes / 1048576).toFixed(1);
  const seconds = Math.round(s.elapsedMs / 1000);
  return `正在加载运行环境 ${index}/${STAGE_TOTAL}（首次约 13MB）· 已下载 ${mb} MB · ${seconds}s`;
};

/**
 * 错误态文案（不含按钮）。
 * @param {{stage: string|null, error: string|null}} s
 * @returns {string}
 */
export const errorText = (s) =>
  `运行环境加载失败（${s.stage || 'runtime'}）：${s.error || '未知错误'}。需可访问 cdn.jsdelivr.net；或本地运行：git clone https://github.com/vickingwu/grider.git 后双击 start.bat / start.command。`;
