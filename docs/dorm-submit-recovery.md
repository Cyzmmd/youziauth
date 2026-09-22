# 打卡提交恢复与失败原因可见性（1.4.4）

## 现场证据（2026-09-22，一次真实的整晚失败）

| 时间 | 事件 |
| --- | --- |
| 21:02:26 / 21:04:07 / 21:04:56 | `login_required` ×3，登录态在反复失效重登 |
| **21:05:40** | `pending.json` 写入 → 当晚**唯一一次**真实 POST 就在此刻发出 |
| 21:05:42 – 21:23:01 | `uncertain` ×14（其中 21:06:08/12/23/31 间隔仅 4 秒 = 手动连点），**一次都没有再发 POST** |

只读复现（21:26，`build/qa/dorm_diagnose.py`）：

- `cqlc/verify` 返回 `{"code":200,"data":{"isArea":true,"tip":"当前在签到范围内"}}`；
- 模拟位置与 `getDormitory` 返回的寝室基准点 `29.821186, 106.426239`（半径 800 米）相距 **21.4 米**；
- `form-instance/select` 中 `qdjg=0`、`dksj` 为空 → 服务器根本没有落库，POST 没有生效。

真实提交一次（21:27，`build/qa/dorm_submit_once.py`，走生产代码路径）：`code=200`、`msg=数据保存成功`、`data.qdjg="1"`、`dksj="2026-09-22 21:27"`；程序 21:28:03 自行 `[signed]` 并清空标记。

**结论：模拟定位与提交 payload 都是正确的。**失败的是那一次 POST 本身，而出问题的代码把两件事做坏了：

1. `submit()` 的响应被 `except Exception: pass` 整体丢弃 —— 服务器说的原因（HTTP 状态 / `msg` / `code`）没有落盘，事后无法复盘；
2. `_readback()` 把「服务器明确回答未签到」和「回查本身失败」混为一谈，都返回 `uncertain` 并保留 `pending` 标记；而 `run()` 只要 `pending` 存在就永远只回查 → **一次瞬时失败锁死整晚**，用户连点十几次手动打卡全部被降级成回查。

## 行为契约（1.4.4 起）

| 场景 | 状态 | `pending` 标记 | 后续 |
| --- | --- | --- | --- |
| save 响应 `data.qdjg == "1"` | `signed` | 清除 | 结束，无需回查 |
| POST 抛异常/响应无确认，但回查 `qdjg == "1"` | `signed` | 清除 | 结束 |
| POST 失败，回查**明确未签到** | `ready` + 真实原因 | 清除（确认没落库，重发不可能重复） | 自动重试，受冷却与次数上限约束 |
| 同上，但失败原因是 `login_required` / `location_required` | 原状态 + 「本次提交未记录」 | 清除 | 需要用户处理，系统仍会重试 |
| POST 失败，**回查本身也失败** | `uncertain` | **保留** | 只回查，绝不盲目重发 |
| 自动重试达上限（3 次） | `uncertain` + 上限说明 | 清除 | 通知用户到学校页面核实或手动打卡 |
| 冷却未满（60 秒） | `ready` + 剩余秒数 | — | 下一个 tick/再次点击即可 |

- `SUBMIT_COOLDOWN_SECONDS = 60`、`SUBMIT_MAX_ATTEMPTS = 3`（`dorm_checkin.py`，按任务 key 计数，含日期）。
- **上限与冷却只约束自动路径**；手动提交永不拒绝 —— 每次重发前都先由回查确认服务器未记录，因此重发不会制造重复打卡。
- 失败原因以受控文案进入 `history.log` / 界面 / 通知，仍然不记录原始报文、坐标或令牌。
- 新增本地文件 `<dorm store>\submit-attempts.json`：只保存当前任务的次数与时间戳，旧任务 key 在下次写入时被丢弃。

## 已知取舍

自动重试耗尽后状态为 `uncertain`，前端按钮文案仍是「回查提交结果」，但此时 `pending` 已清空，点击会真正重新提交。保留前端契约（`desktop_ui/app.js` 的 `dormNames` 与提交按钮白名单）不动，是为了让这次修复只碰状态机；文案与实际动作的这点偏差已在此记录。

## 验证

- `python -m unittest discover -s tests`：**292 tests OK**（原 270，新增/改写 12 项）。
- `node --test tests/test_desktop_ui.cjs`：**36 tests pass**。
- 新增回归测试：
  - `test_save_response_alone_confirms_the_check_in`（save 响应即判成功，不再回查）
  - `test_confirmed_non_submission_reports_the_reason_instead_of_hiding_it`（原因不再被吞）
  - `test_lost_login_is_reported_and_stays_retryable`
  - `test_unknown_outcome_stays_readback_only_even_after_restart`（未知结果仍然绝不盲发）
  - `test_unrecorded_submission_is_retried_on_the_next_automatic_run`（跨引擎重启的重试）
  - `test_automatic_retry_waits_for_the_cooldown`、`test_automatic_retries_are_capped_and_then_reported`
  - `test_a_manual_submission_is_never_blocked_by_the_retry_limits`
  - `test_attempts_are_per_task_and_older_keys_are_dropped`、`test_corrupt_attempt_record_fails_closed`
  - `tests/test_dorm_integration.py::test_unrecorded_save_is_reported_and_then_retried`（真实本地 HTTP 服务器端到端）
  - `tests/test_dorm_api.py::test_submit_reports_the_confirmation_the_school_writes_back`

## 复现

```powershell
.\.tools\desktop-python\Scripts\python.exe -m unittest discover -s tests
node --test tests/test_desktop_ui.cjs
.\build_msi.ps1 -PythonPath .\.tools\desktop-python\Scripts\python.exe -VerifyPayload
```

只读诊断（不提交，只看服务器原话）：

```powershell
.\.tools\desktop-python\Scripts\python.exe build\qa\dorm_diagnose.py
```
