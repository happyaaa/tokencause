# TokenCause Report

- 输入文件：`examples/litellm_sample.jsonl`
- 事件数：5
- 总成本：$2.5200
- 总 token：67600
- 总耗时：86.0s
- 粗略可省：$1.8900
- 优化后估算：$0.6300
- 预算：$2.0000

## 主要发现

- **[warning] 超过预算**：本次运行成本 $2.5200，超过预算 $2.0000。
- **[warning] 昂贵模型可能用于低价值步骤**：例如第 1 行 `search_repo` / `grep` 使用 `claude-fable-5`。搜索、路由、摘要类步骤通常可先尝试便宜模型。
- **[info] 发现重复上下文**：有 1 个 context_hash 被重复使用，额外重复出现 1 次。可以考虑缓存摘要或裁剪重复 context。
- **[info] 文件/文档被反复塞入上下文**：重复最多的是：README.md x2, src/billing.py x2。检查这些内容是否应该压缩成稳定摘要。
- **[warning] 存在失败步骤**：发现 1 个失败/异常事件。失败重试可能造成隐性成本。

## 优先降本动作

1. **把低风险步骤降级到便宜模型**：`read_context, route_request, search_repo` 这类步骤用了昂贵模型。优先把 search/read/route/summary 切到 mini/Haiku 级别模型，再保留主推理步骤使用强模型。 预计节省 $0.9064。
2. **缓存重复上下文或稳定摘要**：同一个 `context_hash` 在一次 run 中重复出现。可以缓存 context pack、文件摘要或 retrieval 结果，避免每轮重新塞完整上下文。 预计节省 $0.4640。
3. **把反复读取的文件压缩成 memo**：有文件/文档被多次放入上下文。对 README、schema、配置文件这类稳定内容生成 memo，后续步骤引用 memo 而不是原文。 预计节省 $0.3151。
4. **给失败重试加预算护栏**：失败事件已经产生真实成本。建议按 run 设置 max retries、per-step budget，并在连续失败后降级为人工确认或更小上下文重试。 预计节省 $0.2045。

## 成本按模型

- `claude-fable-5`：$1.4300，44100 tokens
- `claude-sonnet-4`：$0.6500，13800 tokens
- `gpt-5`：$0.4400，9700 tokens

## 成本按步骤

- `read_context`：$0.8100
- `retry_tool_call`：$0.6500
- `search_repo`：$0.6200
- `route_request`：$0.4400

## 最慢步骤

- `read_context`：33.0s
- `search_repo`：21.0s
- `retry_tool_call`：21.0s
- `route_request`：11.0s

## 失败事件

- 第 4 行 `retry_tool_call` / `claude-sonnet-4`：tool timeout
