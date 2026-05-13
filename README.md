# Transking

这是一个全自动翻译器skill！对话窗口说明使用transking翻译，指定源目录和目标目录即可自动完成分块翻译！

安装方法：在C:\Users\您的用户名\\.qclaw\skills\Transking\ 把项目文件全丢进去，让QCLAW自己整合一下即可！

批量文本翻译工具，基于 LLM，支持断点续传与看门狗自动重启。

**适用于腾讯 QClaw 产品** — Transking 通过 QClaw 内置的 LLM 代理服务调用大语言模型，与 QClaw 生态无缝集成。看门狗脚本利用 QClaw 的 Cron 定时任务机制实现自动监控与重启，无需额外部署调度系统。

> **QClaw** 是腾讯推出的 AI 智能助手产品，内置 LLM 代理服务（默认监听 `127.0.0.1:19000`），提供统一的模型调用接口。Transking 直接调用该代理服务进行翻译，零额外配置。

## 功能特性

* **批量翻译**：自动扫描源目录中的 `.txt` 文件，逐文件分块翻译为中文

* **智能分块**：按 800-1500 字符自动切分，保持段落完整性

* **块级断点续传**：每完成一个分块即时落盘，中断后重新运行可精准从断点继续

* **速率限制应对**：遇到 403/429 自动退出，由看门狗在下一轮重试

* **看门狗监控**：独立进程定期检测翻译状态，自动重启卡住或中断的任务

* **智能续传**：看门狗重启时自动推断起始编号，无需手动指定 `--start`

* **完成后自动质检**：扫描译文，清理 LLM 误混入的思考过程文字

## 项目结构

```
Transking/
├── scripts/
│   ├── auto_translate.py        # 主翻译脚本 v4.2
│   └── _translate_watchdog.py   # 看门狗脚本 v5.3
└── references/
    └── translation_guide.md     # 使用指南
```

## 环境要求

* Python 3.6+

* `requests` 库

* [腾讯 QClaw](https://qclaw.qq.com)（提供 LLM 代理服务，默认监听 `127.0.0.1:19000`）

## 快速开始

### 单文件翻译

```bash
python scripts/auto_translate.py --file "source.txt" --output-dir "./output"
```

### 批量翻译

```bash
python scripts/auto_translate.py \
  --source-dir "./source" \
  --output-dir "./output" \
  --start 1 \
  --end 50
```

### 启动看门狗（推荐）

批量翻译时，建议配合看门狗使用，以应对进程中断和速率限制：

```bash
python scripts/_translate_watchdog.py \
  --source-dir "./source" \
  --output-dir "./output"
```

看门狗建议通过 QClaw Cron 定时任务每 20 分钟运行一次，配置示例：

```json
{
  "name": "Transking Watchdog - <项目名>",
  "schedule": { "kind": "every", "everyMs": 1200000 },
  "payload": {
    "kind": "agentTurn",
    "message": "直接执行以下命令，不要分析，不要解释：\npython scripts/_translate_watchdog.py --source-dir <源目录> --output-dir <目标目录>"
  },
  "delivery": { "mode": "none" }
}
```

> ⚠️ `delivery.mode` 必须设为 `none`，设为 `announce` 会导致隐性退避，影响看门狗正常运行。

## 命令行参数

### auto_translate.py

| 参数                | 默认值                  | 说明                           |
| ----------------- | -------------------- | ---------------------------- |
| `--source-dir`    | -                    | 源文件目录                        |
| `--output-dir`    | （必填）                 | 输出目录                         |
| `--file`          | -                    | 翻译单个文件（与 `--source-dir` 二选一） |
| `--start`         | 1                    | 起始文件编号                       |
| `--end`           | 9999                 | 结束文件编号                       |
| `--model`         | pool-deepseek-v4-pro | LLM 模型名称                     |
| `--chunk-max`     | 1500                 | 单块字符数上限                      |
| `--chunk-delay`   | 30                   | 块间延迟（秒）                      |
| `--no-post-check` | false                | 跳过完成后自动质检                    |

### _translate_watchdog.py

| 参数                | 默认值                  | 说明                  |
| ----------------- | -------------------- | ------------------- |
| `--source-dir`    | （必填）                 | 源文件目录               |
| `--output-dir`    | （必填）                 | 输出目录                |
| `--start`         | 1                    | 起始文件编号（通常由智能续传自动推断） |
| `--end`           | 9999                 | 结束文件编号              |
| `--model`         | pool-deepseek-v4-pro | LLM 模型名称            |
| `--chunk-max`     | 1500                 | 单块字符数上限             |
| `--chunk-delay`   | 30                   | 块间延迟（秒）             |
| `--no-post-check` | false                | 跳过完成后自动质检           |

## 环境变量

| 变量                   | 默认值                                | 说明          |
| -------------------- | ---------------------------------- | ----------- |
| `QCLAW_LLM_BASE_URL` | `http://127.0.0.1:19000/proxy/llm` | LLM API 地址  |
| `QCLAW_LLM_API_KEY`  | （空）                                | LLM API Key |

## 工作流程

### 翻译流程

```
扫描源目录 → 按编号排序 → 逐文件处理
  ↓
跳过已完成的文件（输出目录已有同名 .txt）
  ↓
分块 → 调用 LLM 翻译 → 实时写入 .part 文件
  ↓
文件完成 → .part 重命名为 .txt → 清理进度标记
  ↓
最后一个文件完成 → 写入 _project_done_flag → 执行自动质检
```

### 看门狗检测逻辑

```
项目完成标志存在？ → 通知完成，退出
  ↓
有 .part 文件？
  ├─ 是 → 卡住（>300s 未更新）？→ 杀掉进程，重启翻译
  └─ 否 → 源文件数 > 输出文件数？→ 重启翻译
  ↓
一切正常，无需干预
```

### 智能续传

看门狗重启翻译时，自动推断起始编号：

1. 有 `.part` 文件 → 从最新的 `.part` 文件对应编号开始

2. 无 `.part` 文件 → 从已完成的最大编号 +1 开始

3. 都没有 → 从编号 1 开始

## 速率限制应对策略

采用四级策略：

1. **块重试**：单块失败自动重试 3 次，指数退避（60s → 180s）

2. **主动退出**：遇到 403/429 时创建标记并退出，避免无效重试导致封号

3. **看门狗重启**：看门狗检测到进程退出且项目未完成时，自动重启

4. **速率限制容忍**：重启后仍限流则再次退出，看门狗下一轮再试

## 辅助文件说明

翻译过程中会在输出目录产生以下辅助文件：

| 文件                             | 说明                              |
| ------------------------------ | ------------------------------- |
| `*.txt.part`                   | 翻译进行中的临时文件，完成后重命名为 `.txt`       |
| `*.txt.part.chunks`            | 分块进度标记（如 `3/10` 表示第 3 块/共 10 块） |
| `_rate_limit_flag`             | 速率限制标记，内容为限流发生时的 Unix 时间戳       |
| `_project_done_flag`           | 项目完成标志，内容为 `1`                  |
| `_translate_errors.txt`        | 翻译错误日志                          |
| `_watchdog.lock`               | 看门狗锁文件，防止重复运行                   |
| `_watchdog.log`                | 看门狗运行日志                         |
| `_completion_notification.txt` | 完成通知内容                          |

## 参数调优建议

| 参数              | 建议值   | 说明                       |
| --------------- | ----- | ------------------------ |
| `--chunk-max`   | 1500  | 单块字符数。API 报错多时可调大以减少请求总数 |
| `--chunk-delay` | 15-60 | 块间延迟。频繁触发速率限制时请增加此值      |

## License

MIT
