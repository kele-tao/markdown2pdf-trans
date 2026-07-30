# 更新日志

## [2.0.0] - 2026-07-30

### 🎉 重大重构

完整的代码重构与功能改进。

#### 新增

- **环境变量配置**：AI 服务配置从硬编码改为 `.env` 文件（`AI_API_URL` / `AI_API_KEY` / `AI_MODEL`），启动时自动加载
- **任务生命周期管理**：统一的 `Task` / `TaskState` / `TaskManager`，支持 `PREPARING / READY / RUNNING / PAUSED / STOPPING / STOPPED / SUCCESS / FAILURE` 八种状态
- **结构化 Markdown 分块**：双语对照按 heading / code / list 对齐，不再因 AI 改个空行就错位
- **LRU + TTL 翻译缓存**：避免重复调用 AI，自动清理过期条目
- **AI 调用限流 + 指数退避重试**：信号量限制并发，遇到网络/限流错误自动重试
- **路径安全加固**：zip slip / 软链接 / 路径遍历全部拦截；`.zip` / `.md` 误传有友好提示
- **4 套主题预设**：默认 / 学术 / 商务 / 暗黑，一键切换
- **暗色模式**：随系统偏好 + 手动切换，记忆用户选择
- **拖拽上传 + 实时预览**：上传后立即生成原文/译文 PDF 预览
- **Toast 通知 + 加载动效**：所有错误/成功都有即时反馈
- **进度区增强**：实时显示当前处理文件名、日志按级别上色、日志数量 badge
- **品牌资产**：自定义 logo、hero 插画

#### 改进

- **错误处理**：Pandoc / WeasyPrint 错误按文件捕获，单文件失败不再拖垮整批
- **资源管理**：任务目录自动清理（TTL），翻译缓存自动清理（LRU + TTL）
- **并发安全**：所有共享状态加锁，状态机协作式
- **响应式布局**：平板/手机自适应
- **统一错误响应**：所有 API 错误返回 JSON `{error: ...}`，前端可解析
- **HTML 模板分离**：从 700+ 行 Python 字符串拆到 `templates/index.html`

#### 修复

- 状态机 `request_stop` 信号永远可响应（之前会卡死）
- `get_pdf_page_count` 不再每次预览都写临时文件
- 文件夹上传累加大小检查
- ZIP 头部魔数校验
- 重复 `start_conversion` 现在的状态码是 409 而非 200
- 任务失败后可以重试（`reset_for_retry`）

#### API

| 端点 | 方法 | 描述 |
|---|---|---|
| `/` | GET | Web UI |
| `/ai_status` | GET | AI 服务状态 |
| `/prepare_upload` | POST | 上传文件 |
| `/preview/original` | POST | 原文 PDF 预览 |
| `/preview/translated` | POST | 译文 PDF 预览 |
| `/start_conversion` | POST | 启动转换（支持重试） |
| `/pause/<task_id>` | POST | 暂停 |
| `/resume/<task_id>` | POST | 继续 |
| `/stop/<task_id>` | POST | 停止 |
| `/status/<task_id>` | GET | 轮询状态（含 current_file + logs） |
| `/download/<task_id>` | GET | 下载结果 |

#### 测试

- 38 个 pytest 用例，覆盖：缓存、文件读取、双语对齐、路径安全、上传大小、状态机、限流、重试、并发安全、端到端流程
- 全部通过

---

## [1.0.0] - 2025

### 初版

- 944 行单文件 Flask 服务
- Markdown → PDF 转换（Pandoc + WeasyPrint）
- 6 种语言 AI 翻译
- 三种导出模式（仅原文 / 仅译文 / 双语对照）
- ZIP / 文件夹 / 单文件上传
- 任务暂停/继续/结束
- 实时日志
- 翻译缓存
