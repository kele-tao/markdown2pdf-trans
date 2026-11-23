# markdown2pdf-trans

> 私有部署的 **Markdown 多语言 PDF 交付工具**：结构性翻译 + 出版级排版，一条命令启动，产出的 PDF 可直接对外交付。

[![Python](https://img.shields.io/badge/python-3.12-blue)]() [![Flask](https://img.shields.io/badge/flask-3.x-black)]() [![License](https://img.shields.io/badge/license-Apache--2.0-green)]()

---

## 一、它解决什么问题

把一批 Markdown 文档（单篇或整包 ZIP）翻译成 6 种语言，并输出**排版达到正式出版物水准**的 PDF：

- 翻译按文档**结构**切分（标题/段落/列表/表格/代码块/引用），长文不截断、不超时；
- 每个块携带**上下文**（文档标题、章节路径、前后块摘要），术语表**任务级强制统一**；
- 译文自动做**结构保真校验**（标题层级/列表项数/表格行列/代码块内容/链接/图片/脚注/公式），
  不通过则重试，重试仍失败**保留原文并明确告警**，绝不静默丢内容；
- 输出含**封面、带页码的目录、页眉页脚、页码**，表格跨页表头重复、长代码换行、图片不溢页、标题不孤立；
- 支持**并排**与**上下**两种双语对照版式，分页后原文与译文逐块对应、不裁切。

## 二、快速开始

### 方式 A：本地 Python（推荐开发）

```bash
python -m pip install -r requirements.txt
cp .env.example .env          # 可选：填入 AI_API_KEY；不填则进入离线演示模式
python -m src.app             # 或 python main.py
```

打开 <http://127.0.0.1:5000>。

> **不需要 API Key 也能跑通全流程**：未配置密钥时自动进入「离线演示模式」，
> 由本地 mock 翻译引擎完成切分、上下文、术语、结构校验与排版（不联网）。
> 配置中心可随时切换真实 AI 服务，改完立即生效，无需重启。

### 方式 B：Docker（推荐交付）

```bash
docker compose up -d --build
curl -fsS http://127.0.0.1:5000/api/health
```

数据目录 `./data` 已挂载：任务历史、输出产物、运行期配置都会持久化，重启容器不丢。

## 三、系统依赖

| 依赖 | 用途 | 缺失时 |
| --- | --- | --- |
| Python 3.12 | 运行时 | — |
| pandoc ≥ 3.0 | Markdown → HTML | 页面可用，转换/预览报 `pandoc` 错误 |
| WeasyPrint（含 libpango / libharfbuzz） | HTML → PDF、阿拉伯语塑形 | 同上 |
| 中/日/韩 + 阿拉伯字体 | 多语言不缺字 | 回退系统字体，可能缺字 |

Docker 镜像已内置 pandoc、Pango、HarfBuzz 与 Noto CJK / Noto Arabic 字体。
本机渲染中文/阿拉伯语时请确保系统装有对应字体（Windows 自带微软雅黑、宋体、Tahoma 即可）。

## 四、界面

| 视图 | 能力 |
| --- | --- |
| **工作台** | 三步式：上传（拖拽 ZIP / 文件夹 / 单文件）→ 配置（语言、导出模式、双语版式、4 套主题、页面尺寸/方向/边距/字号/行距/颜色、封面目录页眉页码开关、术语表）→ 转换（实时总进度、当前文件、成功失败数、日志、暂停/继续/取消、单文件重试、下载） |
| **历史任务** | 任务列表、状态、耗时、搜索过滤、重新下载、打开、删除 |
| **配置中心** | AI 地址/密钥/模型/并发/超时可在界面修改，立即生效；密钥**只回显掩码**、不写日志；一键测试连接；运行环境与依赖自检 |

界面为**完全离线**实现：无 Google Fonts、无 CDN、无外部图标字体，图标为内联 SVG，
支持深浅主题、1440 / 768 / 390 三档响应式、键盘可达与可见焦点。

### 版式主题

| 主题 | 定位 |
| --- | --- |
| `default` | 通用技术文档：清晰的字号阶梯与克制留白 |
| `academic` | 论文/研究报告：衬线正文、素雅页眉 |
| `business` | 对外交付：无衬线正文、品牌色标题、深色表头 |
| `modern` | 产品手册：大留白、强对比标题 |

## 五、支持的语言

英语、中文、法语、俄语、西班牙语、阿拉伯语。
阿拉伯语自动启用 RTL：`dir="rtl"`、右对齐、Naskh 字体优先、表格/列表镜像缩进。

## 六、API 一览

统一前缀 `/api`，错误统一为 `{"error": "...", "code": "E_..."}`。完整契约见 `ai-docs/API_Contract/01_API契约.md`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查（pandoc / weasyprint / 字体 / 数据目录 / AI 模式 / 任务统计） |
| GET/PUT | `/api/settings` | 配置中心读取/更新（密钥仅掩码） |
| POST | `/api/settings/test` | 测试 AI 连接 |
| POST | `/api/uploads` | 上传（`type=zip|folder|file`） |
| GET | `/api/tasks` | 任务列表（`limit/offset/state/q`） |
| GET | `/api/tasks/<id>` | 任务详情（文件级状态、失败原因、可选操作） |
| GET | `/api/tasks/<id>/status` | 轻量轮询（进度、计数、增量日志） |
| POST | `/api/tasks/<id>/start` | 启动（语言/模式/主题/术语表/文档要素） |
| POST | `/api/tasks/<id>/pause|resume|cancel` | 暂停/继续/取消 |
| POST | `/api/tasks/<id>/retry` | 重试失败文件（可指定单文件） |
| GET | `/api/tasks/<id>/download` | 下载结果 ZIP |
| DELETE | `/api/tasks/<id>` | 删除任务与产物 |
| POST | `/api/tasks/<id>/preview` | 实时 PDF 预览（返回二进制，带 `X-Page-Count`） |
| POST | `/api/glossary/suggest` | 自动提取候选术语 |

旧版根路径接口（`/prepare_upload`、`/start_conversion`、`/status/<id>`、`/download/<id>`、
`/pause|resume|stop/<id>`、`/preview/original|translated`）保留兼容，内部委托到同一实现。

## 七、架构

```
src/
├── app.py / app_factory.py   应用工厂、蓝图注册、错误处理
├── api/                      HTTP 层（health/settings/meta/uploads/tasks/preview/glossary/legacy）
├── core/                     错误码、结构化日志、运行期配置存储
├── pipeline/                 编排：配置校验 → 翻译 → 排版 → 报告 → 打包
├── rendering/                字体链、主题 CSS、Markdown→HTML、HTML→PDF、双语拼装
├── translation/              翻译引擎（解析/切分/上下文/术语/校验/重试/缓存）
├── task_manager.py           任务状态机 + JSON 注册表持久化
├── uploads.py / file_utils.py / concurrency.py / translation_cache.py
├── templates/index.html      薄模板（页面骨架 + 内联 SVG 图标精灵）
└── static/css|js             设计令牌 + 组件样式 + 8 个前端模块（零构建、零 CDN）
```

设计原则：单文件 ≤ 600 行（`translation/glossary.py` 与 `pipeline/conversion.py` 接近上限，正在拆分）；
配置集中（`src/config.py` + 运行期 `settings_store`）；错误码统一；日志结构化（`M2P_LOG_FORMAT=json`）。

## 八、测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

覆盖范围：

| 测试文件 | 覆盖 |
| --- | --- |
| `test_smoke.py` / `test_hardening.py` / `test_modules.py` | 旧链路冒烟、状态机、并发限流、上传安全、缓存 |
| `test_translation_engine.py` | 长文切分、并发顺序、结构校验重试、取消、缓存、边界输入 |
| `test_translation_parser.py` / `_glossary.py` / `_i18n.py` | Markdown 解析、术语、多语言与 RTL |
| `test_api_contract.py` | 健康检查、配置中心（密钥不泄漏）、上传错误码、任务全流程、预览、术语、旧接口兼容、离线资源 |
| `test_rendering.py` | 4 套主题完整性、页面尺寸/方向/边距、目录页码锚点、封面、RTL、图片内联、双语两种版式、大文档稳定性 |
| `test_task_persistence.py` | 重启恢复、中断标记、损坏注册表容错、清理与删除、列表过滤 |
| `test_samples_e2e.py` | 样例库（表格/代码/图片/脚注/公式）三种导出模式端到端 |

端到端样例文档位于 `tests/fixtures/samples/`。

## 九、已知限制

- **进度粒度**：进度条按文件粒度推进；单文件内部只更新"已完成 N/M 块"日志，不做块级百分比。
- **目录页码**：由 WeasyPrint `target-counter` 计算（已实测 3 页文档目录条目页码正确）。
  若标题文本重复导致锚点歧义，页码可能偏到首个同名标题所在页。
- **公式**：走 pandoc MathML 输出，标准 LaTeX 可用；自定义宏（`\newcommand`）可能退化为纯文本。
- **术语自动提取**：离线模式是启发式（行内代码/标识符/中文 n-gram 词频），可用但不如 AI 精准；
  AI 模式需在配置中心填密钥。提取结果是"候选"，务必人工核对。
- **单文件重试**：重跑该文件的翻译与排版，复用块级缓存但不复用中途块；不重跑的文件产物会合并进同一 ZIP。
- **双语对照**：按结构块配对（同类型 + 同标题级别）；若模型改变了块数量，多出的块会以"未翻译"占位显示。
- **并发模型**：单进程多线程（任务内按块并发）。多副本部署需要外置队列与共享存储，本期未做。
- **Docker**：镜像已含 pandoc/Pango/HarfBuzz/Noto 字体并有 HEALTHCHECK，但本轮环境无 Docker daemon，
  未实机跑过 `docker compose up --build`（compose 语法与依赖清单已人工核对）。
- **UI 自动化**：Playwright 走查目前是独立脚本（1440/768/390、深浅主题、无横向滚动、控制台零报错均已人工验证），
  尚未纳入 pytest 用例。
- **不做**：公网多租户、账号体系、计费、扫描件 OCR、Office 输入。

## 十、路线图

1. 任务级"从断点续跑"（按文件粒度恢复已完成的翻译缓存）。
2. 术语表保存为可复用词库，跨任务共享。
3. PDF 书签（outline）与可点击目录。
4. 多进程 Worker + Redis 队列，支持水平扩展。

## 十一、许可

Apache-2.0，见 [LICENSE](LICENSE)。
