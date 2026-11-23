# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## v4.0.0 — 2026-11-23

### 破坏性变更

- 数据目录从 `output/` 迁移到 `data/`（`data/output/` 存产物，`data/tasks.json` 存任务注册表，
  `data/settings.json` 存界面配置）。旧 `output/` 可通过 `M2P_OUTPUT_DIR` 指回继续使用。
- 新增 `/api/*` 契约；旧根路径接口保留兼容层（内部委托同一实现）。
- `src/conversion.py` 改为兼容 shim，真实编排在 `src/pipeline/conversion.py`。
- `src/biligual_align.py` 的双语对齐被 `src/rendering/bilingual.py` 取代（保留旧模块以兼容测试）。

### 新增

**翻译质量闭环**
- 主链路接入 `src/translation` 引擎：按 Markdown 结构块切分、上下文携带、超长段落按句子边界二次切分。
- 任务级术语表：命中术语强制译法 + 译后一致性核对 + 自动提取候选（AI 优先，离线启发式兜底）。
- 结构保真校验覆盖标题层级、列表项数、表格行列、代码块内容、行内代码、链接/图片地址、脚注、占位符、公式；
  失败自动重试（带「上一轮问题」提示），仍失败保留原文并在任务结果中给出中文原因。
- 失败可见与单文件重试：`FileEntry` 记录每个文件状态/页数/块数/告警/错误/耗时；
  `POST /api/tasks/<id>/retry` 可按文件重试，产物合并进既有 ZIP。
- 阿拉伯语 RTL：`dir="rtl"`、右对齐、Naskh 字体优先、列表/表格镜像缩进。

**出版级排版**
- 封面（标题/副标题/作者/日期/装饰线）、目录（`target-counter` 自动页码）、页眉（文档标题 + 当前章节）、
  页脚、页码 `n / total`。
- 4 套成品主题（默认/学术/商务/现代），页面尺寸（A4/A5/Letter/Legal）、方向、边距、字体族、字号、
  行距、正文/标题/链接/强调色均可调。
- 表格跨页重复表头（`display: table-header-group`）、长代码自动换行、图片 `max-width: 100%`、
  标题 `break-after: avoid`、段落 `orphans/widows`。
- 双语对照重写为块级配对：并排（两列，块内禁止跨页）与上下（原文淡色 + 译文）两种版式。

**任务与体验**
- 任务持久化：JSON 注册表落盘（带 0.5s 写合并），服务重启后历史仍在；中断任务标记 `STOPPED` + 明确原因。
- 历史页：列表/搜索/状态过滤/重新下载/打开/删除。
- 实时进度：总进度 + 当前文件 + 总数/成功/失败计数 + 分级日志（info/warn/error/success + 时间戳）。
- 暂停/继续/取消：Event 协作，取消后已完成部分保留可下载。
- 配置中心：AI 地址/密钥/模型/并发/超时/离线模式可在界面修改，立即生效；密钥仅掩码回显、不写日志；
  一键测试连接；运行环境自检。

**工程与交付**
- 分层重构：`src/core`（错误码/结构化日志/配置存储）、`src/api`（蓝图）、`src/rendering`（排版）、
  `src/pipeline`（编排）。单文件 < 600 行。
- 统一错误码（`E_UPLOAD_* / E_TASK_* / E_AI_* / E_PATH_UNSAFE ...`），前端按 code 精准提示。
- 结构化日志：`M2P_LOG_FORMAT=json` 输出 JSON Lines，字段含 task_id / file / duration_ms / event。
- `GET /api/health`：pandoc、weasyprint、字体数量、数据目录可写、AI 模式、任务统计、磁盘余量。
- 离线演示模式（mock 翻译引擎）：无密钥也能演示完整流程，mock 会走真实切分与结构校验。
- Dockerfile 重写（多阶段构建、非 root 用户、内置 pandoc/Pango/HarfBuzz/Noto 字体、HEALTHCHECK）；
  compose 挂载 `./data` 与 `./fonts`。
- 前端拆分：`index.html` 仅保留页面骨架（含内联 SVG 图标精灵），样式拆为 `theme.css`（设计令牌）与
  `app.css`（组件），脚本拆为 8 个模块；移除 Bootstrap / bootstrap-icons / Google Fonts 全部 CDN 依赖。

### 变更

- 上传文件名清洗不再丢中文（`secure_filename` 之前会把纯中文文件名清空）。
- ZIP 解压增加压缩炸弹防护（解压比 > 200 倍即中止）。
- `pypandoc` 高亮参数按 pandoc 版本自适应（3.2+ 用 `--syntax-highlighting`），消除弃用告警。
- 渲染 CSS 移除 `-weasy-hyphens` 等弃用属性。

### 修复

- 修复 `python src/app.py` 直接跑脚本时相对导入失败（现在三种启动方式都可用）。
- 修复 `[hidden]` 被组件 `display: grid/flex` 覆盖导致「历史任务 / 配置中心」视图叠在工作台上。
- 修复目录锚点在标题重名时重复（`id` 冲突使 WeasyPrint 丢弃 `target-counter`、目录页码漂移）；
  现在锚点按位置编号，原 pandoc id 保留为 `data-anchor`。
- 修复暂停后点取消仍需等整篇渲染完才生效：取消立即进入 `STOPPING`，并在翻译/每种渲染之间加检查点。
- 修复完成态仍显示「取消」按钮。
- 修复翻译结构校验对表格的误判（列数丢失被当成「块结构不一致」），现在给出「表格列数不一致」明确原因。
- 修复 `TaskManager.delete` 与清理线程的语义冲突（管理器层允许删除，业务层负责运行中拦截）。
- 修复封面页占用页码导致正文页码错位的问题。

### 测试

- 新增 `test_api_contract.py`（19 用例）、`test_rendering.py`（22 用例）、`test_task_persistence.py`（9 用例）、
  `test_samples_e2e.py`（4 用例）。
- 新增端到端样例文档集 `tests/fixtures/samples/`（含表格/代码/图片/脚注/公式/引用/链接）。
- 既有 93 个用例全部保持通过。
