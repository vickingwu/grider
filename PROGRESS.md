# ETFer-Clone 项目进度存档

> 最后更新：2026-06-04。新增「网格标的筛选器」功能并完成验证。

## 一、项目概况

- **目标**：复刻 https://etfer.top/（开源项目 jorben/grider），用**免费 AkShare** 替换原 Tsanghi(沧海)付费数据源，本地全栈运行。
- **用户 GitHub fork**：`github.com/vickingwu/grider`
- **本地项目目录**：`c:\Users\wuminqia\Documents\etfer-clone\`
- **技术栈**：后端 Flask(Python 3.14 + venv) + 前端 React/Vite。

## 二、启动方式

- 后端：`cd backend; .venv\Scripts\python.exe main.py --port 5000 --host 127.0.0.1`
- 前端：`cd frontend; npm run dev`（端口 3000，已配 /api 代理到 5000）
- 访问：http://localhost:3000/

## 三、数据源说明（重要）

- 全部用 **AkShare**，无需 token、无需 VPN。
- 主源东方财富(eastmoney)在本机常被限流（日志会刷 "调用失败...Connection aborted"），
  **已做新浪(sina)自动兜底**，数据照常返回，报错不影响结果。
- 日线 10 年 sina 约 1–4s；个股名称表 / ETF 名称表已加长缓存。

## 四、本轮已完成的修改（全部验证通过）

1. ✅ 去掉顶部导航：`src/app/AppLayout.jsx` 移除 `<AppHeader />`（logo/ETFer.Top/副标题/GitHub）。
2. ✅ 去掉背景水印：`AppLayout.jsx` 移除 `<Watermark />`。
3. ✅ 去掉底部：`AppLayout.jsx` 移除 `<AppFooter />`（重要声明/系统特点/风险提示/版权）。
4. ✅ 首页精简：`src/pages/HomePage/HomePage.jsx` 移除 `<HeroSection />`，直接显示参数设置。
5. ✅ 支持个股 + 通用提示：`ETFSelector.jsx` placeholder 改为"如：510300、603137、SPY"；
   `ETFInfoSkeleton.jsx` 文案"正在加载标的信息..."。
6. ✅ 统一标的名称：后端 `data_service._lookup_name` 重写——ETF 用内置映射+新浪ETF表，
   个股用全量 A股 code→name 表（带缓存+重试+个股信息接口兜底）。验证：603137 → "恒尚节能"
   在标题/页头/所选标的卡片均正确显示。
7. ✅ 去掉"分享报告"按钮：`AnalysisPage.jsx` 移除 Share2/useShare/handleShare 及移动端+桌面端两处按钮；
   `AnalysisReport.jsx` 去掉 useShare 与 showShareButton。

### 问题 #6（603137 回测无数据）—— 已修复
- 根因：分析按"最新价"建网格，回测回放历史区间，起始价落在网格区间外 → 0底仓 → 0成交。
- 修复：`backtest_service.py` 新增 `_realign_grid_to_period()`，当回测起始价不在网格区间内时，
  以回测首根K线均价为中心、按原步长重建网格。仅在用户未显式自定义价格区间时触发。
- 验证：603137 回测现显示 12 笔交易、胜率 75%、完整交易记录表 + 图表（收益 -5.71% 属真实下跌行情）；
  510300 不受影响（16 笔，+3.61%，未触发重对齐）。

## 五、10 年时间跨度支持（已完成）

- 后端校验：`backtest_service.py` 上限 120天 → **3660天(10年)**。
- 前端校验：`GridParameterSettings.jsx` 同步改为 10 年。
- 性能：`data_service.get_5min_kline()` 对 **跨度 >120 天自动改用日线**（避免分钟数据量过大）。
- 实测：510300 十年回测 **不崩溃**，约 2–19s（首次含取数），2426 根日线、40 笔交易、
  总收益 51.35%、年化 5.16%（重对齐生效，正常出数）。

## 六、数据源优先级优化（已完成）

- `data_service._fetch_daily`：**新浪(sina) 改为主源，东方财富(eastmoney) 改为兜底**。
  本环境新浪稳定快速，日志不再刷 "调用失败...Connection aborted"。
- 5 分钟 K 线失败日志由 warning 降为 info（仅 eastmoney 有分钟数据，失败时静默回退日线）。
- 验证：510300 分析 200 响应 95ms，日志仅 INFO，无失败刷屏。

## 七、回测说明动态文案（已完成）

- `BacktestGuide.jsx` 接收 `period` prop，根据实际回测周期与数据粒度动态生成文案：
  - 按 总bar数/交易日数 判断粒度（≥2 视为5分钟，否则日线）
  - 文案示例："基于 2026-04-20 至 2026-06-03（共30个交易日）的日线数据进行模拟回测"
- `BacktestTab.jsx` 传入 `backtestResult.backtest_period`，并清理了渲染中的调试 console.log。
- 验证：510300 短周期回测显示"...30个交易日...日线..."（本环境5分钟被限流自动降级日线，文案如实反映）。

## 八、性能优化（已完成）

- `_fetch_daily` 改为"全量历史下载一次 + 内存切片"，消除单次分析中重复下载整段历史。
- 名称表（ETF/个股）持久化到磁盘 `backend/cache/*.json`（7天有效），跨重启复用，
  省去每次启动 ~15s 的全量个股名称表下载。
- 实测：603137 冷启动 32.9s → 2.3s；热缓存均 <0.3s。

## 九、编辑参数回测 500 修复（已完成）

- 根因：`validate_backtest_request` 中 `if 'backtestConfig' in data` 当值为 `None` 时，
  后续 `'commissionRate' in None` 抛 `TypeError` → 500。
- 修复：改用 `data.get('backtestConfig')` + 类型/None 守卫。
- 验证：HTTP 500→200；浏览器"编辑参数→保存并重新回测"正常出数（9笔、胜率87.5%）。

## 十、首页分析前自定义参数（功能B，已完成）

- 首页新增勾选项"分析前自定义网格参数"（`ParameterForm.jsx`，持久化 `editParamsFirst`）。
- 经 URL `&edit=1` 透传（`url.js` 编解码）→ `AnalysisPage` → `AnalysisReport`：
  勾选时分析完成后自动切到"回测分析"标签并展开参数编辑面板（预填 ATR 计算的默认值）。
- 实现：`AnalysisReport` 用一次性 effect 切 tab；`GridParameterSettings` 用一次性 effect 展开编辑（`autoEditParams`）。
- 分析后再编辑能力保留不变（原"编辑参数"按钮照常）。
- 验证：首页勾选→510300 自动进入编辑面板（预填日期/区间/步长/单笔）→保存并重新回测成功（16笔、胜率84.6%，无500）。

## 十一、编辑参数后回测结果全 0 修复（已完成）

- 现象：编辑参数（尤其选历史回测区间）后，区间收益/交易次数/胜率全为 0，仅"持有不动"有数。
- 根因：功能B/上一轮 #6 修复时加了 `if not user_set_price` 守卫——但编辑面板**总是**带价格区间，
  导致网格区间对齐（realign）在编辑流程里**永不触发**；当回测区间价格与网格不重叠时 → 0 底仓 0 成交。
- 修复：去掉 `user_set_price` 守卫，**总是调用** `_realign_grid_to_period`；该函数仅在
  "回测起始价落在网格区间之外"时才重建网格，价格本就在区间内时尊重用户设置（不动）。
- 验证：
  - 历史区间(2024-06~09，价~3.3) + 当前价网格(4.7) → 修复前 0 笔；修复后 21 笔、+9.11%，realigned=True。
  - 近期区间 + 当前价网格 → 16 笔、+2.92%、realigned=None（未误触发，尊重用户网格）。
  - 浏览器：首页勾选→自动编辑→默认近30日回测 20 笔、胜率87.5%、+4.45%，图表与交易记录正常。

## 十二、网格标的筛选器（已完成）

- **目标**：从"单标的体检"扩展出"批量挑选"——一键对主流 ETF 候选池评分排序。
- **后端**：
  - `constants.py` 新增 `SCREENER_CANDIDATES`（约 50 只：宽基/行业/跨境/红利/商品债券，可自行增减）。
  - `services/screener_service.py`：复用 `ETFAnalysisService` + `SuitabilityAnalyzer`，
    `ThreadPoolExecutor`(4 并发) 批量评分，结果按总分降序，整体缓存 1 小时。
  - `routes/screener_routes.py`：`GET /api/screener`（?refresh=1 强制重算）、`/api/screener/candidates`。
  - 蓝图注册 `url_prefix=/api/screener`；`__init__.py` SPA 路由白名单加 `screener`。
- **前端**：
  - 新页面 `pages/ScreenerPage/`，路由 `/screener`，首页右上角入口按钮。
  - 表格：排名/代码/名称/分类/总分/ATR%/波动率%/ADX/日均成交额/结论；分类筛选 + 跳转分析。
  - `api.js` 新增 `runScreener()`。
- **过程中修复的底层稳定性问题**（对全工具有益）：
  1. `main.py` 启动加 `threaded=True`——原单线程，长请求会阻塞整个服务。
  2. `data_service.py` 给 requests 注入 12s 默认超时——akshare 底层无超时，网络异常会无限卡死。
  3. 批量评分放到**独立线程**执行——规避"在 werkzeug 请求线程里嵌套线程池"导致的死锁
     （现象：直接跑 17s 完成，但在 Flask 请求里跑到第 8 个标的就卡死）。
  4. `_ETF_NAME_MAP` 补充 LOF 名称（161226 白银LOF / 501018 南方原油LOF）。
- **验证**：浏览器实测 49 只全部评分成功，耗时 ~12-15s，排序合理
  （创业板/纳指/中证500 高分 98-99；国债/红利/银行被判"存在严重缺陷"）；缓存二次秒出。

## 十三、其它本轮小改

- **回测图表 tooltip 显示完整年份**：`BacktestCharts.jsx` 两张图（行情与交易、收益对比）
  hover 文案由"月-日 时:分"改为"年-月-日 时:分"，避免跨年回测看不出年份。
- **移除免责声明弹窗**：`shared/utils/disclaimer.js` 的 `checkDisclaimerStatus()` 直接返回 true
  （本工具自用，不再每次进入弹"同意并继续"）。
- **回测默认近半年**：`backtest_service.py` 未指定日期时默认最近 182 天。
- **网格策略↔回测联动**：`AnalysisReport.jsx` 新增 `effectiveGrid`，回测中编辑的网格会同步到
  "网格策略"标签显示（带自定义提示横幅）；切换标签不再丢失编辑参数（`backtestMounted` 保持挂载）。

## 十四、待办

- （可选）邮件发送报告(SMTP)功能尚未做。
- （可选）港股 / 美股支持：当前数据层仅接通 A 股（含 ETF）；AkShare 有免费港美股接口，
  需改 `_classify`、取数分支、交易日历、币种/最小交易单位等（用户已确认想加，暂未开工）。

## 临时文件

- 已清理 `backend\_probe.py` / `test_screen*.py` / `test_screener.py` / 各 `*_result.txt` 等。
