# ETFer-Clone 项目进度存档

> 最后更新：2026-06-05。与博主对齐调查(不跳首笔) + 手续费按类型 + 15日预设。

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

## 十四、均线策略回测（已完成）

- **目标**：在网格回测之外，新增一种完全不同的**趋势跟随**回测方式，独立页面、不与网格流程复用。
- **策略**：单均线——收盘价上穿所选均线买入（按仓位比例），下穿清仓。
- **后端**：
  - 新增 `algorithms/backtest/ma_engine.py`：`calculate_ma`(SMA/EMA) + `MABacktestEngine`，
    复用 `KBar/TradeRecord/FeeCalculator`，输出结构对齐网格回测（trade_records/equity_curve/final_state + ma_series）。
  - `backtest_service.py`：新增 `run_ma_backtest()` + `_resolve_date_range()`(抽出复用) + `_format_ma_result()`；
    复用 `MetricsCalculator`（grid_count 传 0）。
  - `grid_routes.py`：新增 `POST /api/grid/ma-backtest`，按代码 `determine_country` 解析交易所/类型，
    入参 `{etfCode,totalCapital,maParams{period,maType,positionRatio},startDate,endDate}`。
  - `__init__.py` SPA 白名单加 `ma-backtest`。
- **前端**：
  - 新页面 `pages/MABacktestPage/`（页面 + `MABacktestChart.jsx` 价格/均线/买卖点叠加图），路由 `/ma-backtest`，首页入口按钮。
  - 周期预设 5/20/50/99/128/225 + 自定义；SMA/EMA 切换；仓位 10%~100% 滑块；可选日期。
  - `api.js` 新增 `runMABacktest()`。
- **验证**：浏览器实测 510300 SMA20 → 13 笔、+0.73%（持有 +6.61%，震荡市均线被洗，正常）；
  SMA50 → 3 笔、贴合趋势；图表/指标/交易表均正常，数字与后端直测一致。

## 十五、本轮其它修复

- **#2 删除底部免责声明**：`AnalysisReport.jsx` 移除底部黄色「重要声明」块（`Disclaimer.jsx` 文件保留但已不引用）。
- **#3 网格区间跨度显示 N/A 修复**：根因是回测重建网格时 `price_range` 只写 `{lower,upper}`、漏了 `ratio`，
  导致"网格策略"标签显示回测网格(effectiveGrid)时 `formatPercent(ratio)` 返回 N/A。
  已在 `_realign_grid_to_period` 与 `_apply_custom_grid_params` 两处补回 `ratio=(upper-lower)/基准价`。
- **#4 市场环境分析/策略调整建议**：确认为**动态规则驱动**（波动率/ADX/网格数/资金利用率阈值触发），非写死，
  按用户要求保持现状（仅"ATR算法优势"4条科普文案是写死的，合理保留）。

## 十六、均线长周期预热修复 + 日期默认值（已完成）

- **问题1**：均线回测页日期控件默认空白，用户不知道默认区间是近半年。
  - 修复：`MABacktestPage.jsx` 初始化 startDate/endDate 为最近 182 天（已预填、可改）。
- **问题2**：选 128/225 日均线报 400（默认近半年只有 ~120 根日线，不够算长周期均线）。
  - 根因：之前 `period >= len(kline_data)` 直接报错。
  - 修复（行业标准"均线预热"）：`run_ma_backtest` 在回测开始日**之前额外多取 `period+5` 个交易日**
    （`_shift_trading_days_back` 算预热起点），这段只喂均线、不参与买卖/收益统计；
    `MABacktestEngine.run(trade_start_index=...)` 支持预热段；`_format_ma_result` 把均线序列切片到回测窗口。
  - 验证：近半年区间 + SMA225 → HTTP200、5笔、均线全程有值；SMA128 → 9笔；SMA20 → 17笔。

## 十七、均线标的筛选器（已完成）

- **目标**：类比网格筛选器，对相同 50 只 ETF 快速挑出适合均线交易的标的。
- **方案**（用户确认 A3+B）：页面顶部选**均线类型(SMA/EMA)+周期(5/20/50/99/128/225/自定义)**，
  全部用同一参数回测；默认按**超额收益(策略−持有)**降序排序。
- **后端**：
  - 新增 `services/ma_screener_service.py`：复用网格筛选器的"独立后台线程 + 缓存"架构，
    每只标的调 `BacktestService.run_ma_backtest` 评分；缓存按"类型+周期+资金+仓位"分键。
  - `routes/screener_routes.py` 新增 `GET /api/screener/ma`（period/maType/capital/positionRatio/refresh）。
- **前端**：新页面 `pages/MAScreenerPage/`，路由 `/ma-screener`，首页入口按钮；
  表格列：超额/策略/持有/回撤/交易次数/胜率 + 分类筛选 + 跳详细回测；`api.js` 新增 `runMAScreener()`。
- **关键修复（重要）**：发现真正的崩溃根因——akshare 依赖的 **`py_mini_racer`(V8 引擎) 非线程安全**，
  并发跑回测会触发 `IsConfigurablePoolInitialized()` V8 多线程初始化崩溃（此前网格筛选/回测偶发卡死同源）。
  → 均线筛选器改为**串行执行**(`_MAX_WORKERS=1`，仍在独立线程内，避免阻塞 werkzeug)，稳定可靠。
- **验证**：浏览器实测 SMA20 → 49/49 成功、耗时 ~42s、白银LOF +37% 超额居首，排序合理；缓存二次秒出。

## 十八、自定义标的列表（替换热门标的，已完成）

- **需求**：网格(首页)与均线回测页把固定"热门标的"改为**用户自定义代码列表**——可添加代码、点击填入输入框、可删除。
- **实现**：
  - 新增通用组件 `features/etf/components/CustomCodeList.jsx`：用 `usePersistedState` 存 localStorage(`customCodeList`)，
    芯片式展示，支持「添加」(输入+回车)、点击填入(onSelect)、删除(×)；首页与均线页**共享同一份列表**。
  - `ETFSelector.jsx`：移除写死的 `hotETFs` 与 `/api/info/popular` 拉取，改用 `<CustomCodeList>`。
  - `MABacktestPage.jsx`：标的代码输入框下方加入 `<CustomCodeList>`。
- **验证**：首页添加 512480 → 芯片+输入框+localStorage 均更新；切到均线回测页，自定义列表同步显示并可点击填入。

## 十九、筛选器表格排序（已完成）

- **需求**：网格/均线筛选器表格支持点击表头切换升/降序。
  - 均线：超额收益、策略收益、持有收益、最大回撤、交易次数、胜率
  - 网格：总分、ATR%、波动率%、ADX、成交额、结论
- **实现**：通用 Hook `shared/hooks/useSortableData.js`（点同列切换升降、点新列默认降序、null 始终在后）
  + 通用表头组件 `shared/components/ui/SortableTh.jsx`（带 ↑/↓ 指示），两页共用，纯前端排序。
- **验证**：网格按波动率降序(116→73→71)→升序(1.6→2.2→10.5)；均线按交易次数降序(26→25→24)。

## 二十、前复权 + 均线图修正 + 均线筛选区间（已完成）

- **#2 行情改前复权(qfq)**：`data_service._fetch_full_sina` 个股 `stock_zh_a_daily(adjust="qfq")`、
  东财兜底个股/ETF 均 `adjust="qfq"`；缓存键加 `:qfq` 避免与旧的不复权数据混用。
  消除分红送转价格跳变，与主流回测口径对齐（ETF 新浪接口不支持复权参数，保持原样）。
- **#1 均线回测图 tooltip 日期/价格错位**：`MABacktestChart.jsx` 原以 "MM/DD" 作 X 轴 key，跨年同月日碰撞→tooltip 取错点；
  日线还误带 15:00。改为以唯一完整日期作 key（X 轴 tickFormatter 显示 MM/DD），tooltip 显示纯日期。
  验证：hover 显示 "2026/03/04 价格4.610 SMA20 4.702"，一致。
- **#3 均线筛选器默认 2020-01-01 至今 + 可选区间**：`ma_screener_service`/`screener_routes` 透传 start/end，
  缓存键含日期；前端 `MAScreenerPage` 加日期选择(默认2020至今)。
  **耗时实测约 40s，与近半年版几乎相同**（数据层"全量下载一次+内存切片"，换区间不增网络请求）。

## 二十一、指数回测支持（已完成）

- **背景**：用户对比公众号「猫笔叨的读后感专区」科创50回测，结果差距大。定位发现其"长期持有收益71.74%"、
  数据从2020-01-01起 —— 用的是**科创50指数(000688)**，而非 ETF 588000（该ETF 2020-11才上市、错过上半年大涨）。
- **实现**：`data_service` 新增 `_INDEX_MAP` 白名单（科创50/沪深300/中证500/中证1000/上证50/创业板指/创业板50/北证50等），
  避免与深市个股 000xxx 冲突；`search_by_ticker` 返回 type=INDEX；`_fetch_full_sina` 对 INDEX 用 `stock_zh_index_daily`，
  东财兜底用 `index_zh_a_hist`。`MABacktestPage` 用 `useSearchParams` 读取 `?code=` 初始代码。
- **验证**：000688 指数 2020-01-02~2026-05-29 持有收益 **= 71.74%**，与博主图完全一致 → 数据源确认正确。
  （初版策略收益仍有差距，根因后在第二十四节定位并修复。）

## 二十二、候选池重整（52只，已完成）

- **原则**：宽基用**指数**（历史更长、与对比口径一致）；ETF 跟踪相同指数的只保留指数；去重、去债券。
- **构成**：宽基指数9（沪深300/中证500/上证50/创业板指/科创50/中证1000/创业板50/北证50指数 + 中证2000用ETF563300）
  / 行业主题25（新增 化工/传媒/航空航天/卫星/家电/有色金属/现金流）/ 红利2 / 跨境海外11（新增 中韩半导体/印度LOF/标普生物科技LOF）
  / 商品能源5（新增 华宝油气LOF）。
- **去掉**：胡志明越南(无免费数据源)、巴西(仅半年数据)、国债、重复的纳指/黄金/恒生/科创板50ETF。
- **LOF 名称**：`_ETF_NAME_MAP` 补充 华宝油气/标普生物科技/印度/白银/南方原油 等 LOF 与指数名称。
- **分类按钮**：两个筛选器「商品债券」→「商品能源」。
- **验证**：全 52 只 MA 筛选 52/52 成功、0 失败、名称全部正确解析；耗时约 81s(2022-2026)。

## 二十三、自定义标的增强（已完成）

- **显示名称**：新增轻量 `GET /api/info/batch-names`（仅查名、复用缓存、不取行情），
  `CustomCodeList` 批量解析并在芯片显示"代码+名称"，模块级缓存跨页面/实例复用。
- **均线回测默认2020**：`MABacktestPage` 默认日期改 2020-01-01 至今。
- **均线筛选器「详细回测」带入代码** 修复：链接改 `/ma-backtest?code=XXX`，回测页读取该参数。
- **样式**：自定义标的去掉冒号、独占一行（容纳更多代码再换行）。

## 二十四、指数回测最小单位 Bug 修复 + 去仓位 + 交易口径（已完成）

- **根因（关键 Bug）**：均线引擎 `min_unit`（最小交易单位）对**指数**误用 100（一手）。
  科创50指数点位 1000+，10万本金 ÷ 1750 ≈ 57 份，`57 // 100 = 0` → **买入数量被取整成0、信号被丢弃**，
  导致交易变少、收益虚低（95% vs 博主134%）。
- **修复**：`MABacktestEngine` 增加 `sec_type` 参数，指数(INDEX) `min_unit=1`（指数本不能按手交易，纯策略模拟）。
- **验证（彻底对齐）**：000688 SMA20 2020-01-01~2026-05-31 → 总收益 **135.04%**(博主134.28%)、
  往返 **83**(博主82)、回撤 **29.69%**(博主29.61%)、胜率 24.1%(博主23.2%) —— 几乎完全一致，确认算法正确。
- **去掉仓位比例（方案A）**：单均线全进全出固定满仓，移除前后端 `position_ratio`/滑块及相关参数。
- **交易次数改往返口径**：主数字显示往返次数(=卖出次数，与博主82一致)，括号保留买/卖明细；均线筛选器同步(`round_trips`)。

## 二十五、与博主对齐调查 + 手续费按类型（已完成）

- **背景**：用户对比公众号博主科创50(000688)均线回测，SMA20基本对上但 SMA15 差很大(99% vs 149.75%)。
- **深度排查（不倒推、逐项验证）**：
  - 数据源正确（持有收益 71.74% 完全一致）。
  - SMA15 差异定位到 **2020-01-22 一笔买入→02-03 春节暴跌亏-17.2%**（均线刚满15根生效首日的信号 + 节后跳空）。
  - **博主规则破解**（实测100%吻合）：当日均线+当日收盘成交 + **跳过第一笔交易**(只统计后续90笔) + 指数免佣。
    零手续费+跳首笔：SMA15=149.76%/往返90、SMA20=134.29%/往返82，与博主 149.75%/134.28% 完全一致。
- **关于"跳过第一笔"是否采纳——经多标的实测后决定【不采纳】**：
  - 该规则影响方向不定且无金融逻辑：纳指ETF第一笔盈利，跳过反而 -16.5%；半导体第一笔亏损，跳过 +14.7%。
  - 跳过会破坏横向可比性（筛选器排序失真），故保持"完整交易、不跳过"。算法本身已验证正确。
- **采纳的改动：手续费按证券类型区分**（均线回测专属，网格回测不变）：
  - `FeeCalculator` 增加 `apply_min` 开关。
  - 指数(INDEX)：免佣(费率0、无最低)；ETF：0.02%费率、**去掉最低5元**；个股(STOCK)：0.02%+最低5元。
  - 验证：000688 指数免佣后 SMA20=142.84%(零费完整交易，内部自洽)。
- **均线周期预设新增15日**（5日右侧）：均线回测页 + 均线筛选器 `PRESET_PERIODS=[5,15,20,50,99,128,225]`。
- **均线回测页自定义标的独占整行**（容纳更多代码再换行）。

## 二十六、待办

- （可选）邮件发送报告(SMTP)功能尚未做。
- （可选）港股 / 美股支持：当前数据层仅接通 A 股（含 ETF/指数/LOF）；用户已确认想加，暂未开工。

## 临时文件

- 已清理 `test_*.py` / `verify*.py` / `probe_*.py` / 各 `*_result.txt` / `*.txt` 探针输出 / `*.jpeg` 截图等。
