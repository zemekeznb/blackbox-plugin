# NOTES-046 独立 MCP 错误探针

仅供明确授权的覆盖测试。无 Hook、数据库、文件读取、网络连接或模型调用；不会向活动库注入事件。只有经当前桌面实际调用并由宿主 Hook 送达，才计为真实集成证据。

正常本机安装：

```powershell
codex plugin marketplace add './tools/error-probe' --json
codex plugin add blackbox-error-probe@blackbox-error-lab --json
```

工具 `blackbox_error_probe.run_case` 只接受 `case`：`success`、`error`、`log_success`、`json_error`、`protocol_error`。前四种依次返回正常结果、合成明确错误、成功读取错误日志的反例、实际 JSON 解析失败的结果；最后一种返回 JSON-RPC 错误，不伪装成工具执行结果。使用固定标记核对真实来源、字段和检测，不根据正常返回推断错误也可送达。

先运行 `python -m unittest discover -s tests -p test_error_probe.py`。独立 stdio 测试及 app-server 工具发现均不能代替桌面 Hook 验收。若当前回合没有发现新工具，须在宿主正常刷新工具后继续，不改宿主、不额外启动模型、不通过 Bash 调用服务器冒充 MCP 工具调用。
