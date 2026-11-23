---
title: 交付样例文档
author: 文档工程组
date: 2026-11-23
---

# 交付样例文档

本文件用于验证 `markdown2pdf-trans` 的端到端链路：**标题层级**、*列表*、表格、代码块、
图片、脚注、数学公式与引用块。

## 1. 结构清单

- 一级列表项
  - 嵌套列表项（应保持缩进层级）
  - 含行内代码 `config.yaml` 的列表项
- 有序列表：
  1. 第一步：准备环境
  2. 第二步：执行转换
  3. 第三步：校验产出

## 2. 表格（跨页表头需重复）

| 模块 | 职责 | 关键文件 | 备注 |
| --- | --- | --- | --- |
| 翻译引擎 | 结构切分、上下文、术语、校验 | src/translation/engine.py | 支持并发与取消 |
| 渲染层 | 主题、封面、目录、页眉页脚 | src/rendering/css.py | 字体内嵌 |
| 任务层 | 状态机、持久化、进度 | src/task_manager.py | 重启不丢 |
| API 层 | 上传、启动、预览、下载 | src/api/*.py | 统一错误码 |
| 流水线 | 编排、汇总、打包 | src/pipeline/conversion.py | 单文件失败隔离 |

## 3. 代码块（不得翻译，长行需换行）

```python
from src.app_factory import create_app

app = create_app()
client = app.test_client()
response = client.post(
    "/api/uploads",
    data={"type": "zip", "zipfile": (buffer, "docs.zip")},
    content_type="multipart/form-data",
)
print(response.get_json()["task_id"], "a very long line that should wrap instead of overflowing the page width in the generated PDF output")
```

行内代码：`weasyprint.HTML(string=html).write_pdf()`。

## 4. 引用与提示

> 重要提示：术语表命中后会强制使用指定译法，翻译完成后自动核对一致性。
>
> 若结构校验未通过，系统会重试；重试仍失败则保留原文并给出明确告警。

## 5. 数学公式

行内公式 $E = mc^2$，块级公式：

$$
\int_0^1 x^2 \, dx = \frac{1}{3}
$$

## 6. 图片

![示例图](assets/sample.png)

如果图片缺失，渲染层会跳过并继续，不会中断整批任务。

## 7. 脚注

结构校验覆盖脚注标记[^note]，脚注内容本身也需保留。

[^note]: 脚注：导出的 PDF 必须保留脚注引用关系。

## 8. 链接

参考 [项目说明](https://example.com/docs) 与 <https://example.com>。
