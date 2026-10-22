/**
 * EngineGate 横幅文案（design.md §6.5 原文；在线版专用）。
 */
import { INIT_STAGES, STAGE_LABELS } from '@shared/services/engine/engineConfig';

/** 阶段分母（n/5） */
export const STAGE_TOTAL = INIT_STAGES.length;

/** 本地版获取方式（错误态与就绪态共用） */
export const LOCAL_FALLBACK_TEXT =
  '或改用本地版：git clone https://github.com/vickingwu/grider.git 后双击 start.bat（Windows）/ start.command（macOS），详见 README「快速开始」。';

/** 就绪态文案 */
export const READY_TEXT = '在线版已就绪 · 数据来源：腾讯财经 / 东方财富公开接口 · 回测使用日线';

/** 就绪横幅折叠为小药丸的延迟（毫秒） */
export const READY_COLLAPSE_MS = 5000;

/** 重试按钮文字 */
export const RETRY_LABEL = '重试';

/**
 * 加载态文案。
 * @param {{stage: string|null, transferBytes: number, elapsedMs: number}} s
 * @returns {string}
 */
export const loadingText = (s) => {
  const index = Math.max(INIT_STAGES.indexOf(s.stage), 0) + 1;
  const label = STAGE_LABELS[s.stage] || STAGE_LABELS[INIT_STAGES[0]];
  const mb = (s.transferBytes / 1048576).toFixed(1);
  const seconds = Math.round(s.elapsedMs / 1000);
  return `正在加载在线运行环境（阶段 ${index}/${STAGE_TOTAL}：${label}）… 首次加载需下载约 30MB 运行环境，之后浏览器会缓存。已下载 ${mb} MB · ${seconds}s`;
};

/**
 * 错误态文案（不含按钮）。
 * @param {{stage: string|null, error: string|null}} s
 * @returns {string}
 */
export const errorText = (s) =>
  `在线运行环境加载失败（${s.stage || 'runtime'}）：${s.error || '未知错误'}。请确认网络可访问 cdn.jsdelivr.net 后点击「${RETRY_LABEL}」；${LOCAL_FALLBACK_TEXT}`;
