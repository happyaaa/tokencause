# TokenCause Report

- 输入文件：`examples/sample_trace.jsonl`
- 事件数：5
- 总成本：$2.6700
- 总 token：86100
- 总耗时：101.0s
- 粗略可省：$2.0025
- 优化后估算：$0.6675
- 预算：$2.0000

## 主要发现

- **[warning] 超过预算**：本次运行成本 $2.6700，超过预算 $2.0000。
- **[warning] 昂贵模型可能用于低价值步骤**：例如第 1 行 `plan` / `none` 使用 `claude-fable-5`。搜索、路由、摘要类步骤通常可先尝试便宜模型。
- **[info] 发现重复上下文**：有 1 个 context_hash 被重复使用，额外重复出现 3 次。可以考虑缓存摘要或裁剪重复 context。
- **[info] 文件/文档被反复塞入上下文**：重复最多的是：src/auth.py x5, README.md x3, src/api.py x3。检查这些内容是否应该压缩成稳定摘要。
- **[warning] 存在失败步骤**：发现 1 个失败/异常事件。失败重试可能造成隐性成本。

## 优先降本动作

1. **缓存重复上下文或稳定摘要**：同一个 `context_hash` 在一次 run 中重复出现。可以缓存 context pack、文件摘要或 retrieval 结果，避免每轮重新塞完整上下文。 预计节省 $0.8714。
2. **把反复读取的文件压缩成 memo**：有文件/文档被多次放入上下文。对 README、schema、配置文件这类稳定内容生成 memo，后续步骤引用 memo 而不是原文。 预计节省 $0.4124。
3. **把低风险步骤降级到便宜模型**：`plan, search_repo, summarize_findings` 这类步骤用了昂贵模型。优先把 search/read/route/summary 切到 mini/Haiku 级别模型，再保留主推理步骤使用强模型。 预计节省 $0.3432。
4. **给失败重试加预算护栏**：失败事件已经产生真实成本。建议按 run 设置 max retries、per-step budget，并在连续失败后降级为人工确认或更小上下文重试。 预计节省 $0.2273。
5. **先优化最贵步骤**：`fix` 是当前最大成本来源。先对这个步骤做 prompt 裁剪、上下文上限和模型路由，收益会比平均优化所有步骤更高。 预计节省 $0.1483。

## 成本按模型

- `claude-fable-5`：$2.5900，78400 tokens
- `gpt-5-mini`：$0.0800，7700 tokens

## 成本按步骤

- `fix`：$1.2000
- `search_repo`：$0.5100
- `test`：$0.4600
- `plan`：$0.4200
- `summarize_findings`：$0.0800

## 最慢步骤

- `fix`：40.0s
- `search_repo`：22.0s
- `plan`：18.0s
- `test`：15.0s
- `summarize_findings`：6.0s

## 失败事件

- 第 5 行 `test` / `claude-fable-5`：pytest failed: timeout
