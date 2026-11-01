# 长句与 ASR 幻觉治理：下班交接

> 恢复说明：用户随后要求 continue，工作已恢复。下面保留暂停时的历史快照；
> 当前行为和验证应以 [验证记录](2026-11-01-caption-integrity-validation.md) 与代码为准。
> 下文两项审查问题现已修复并有回归，不再作为未修复事项。

状态：用户于 2026-11-01 要求保存并暂停。代码保存在本机 `dev` 工作区，未提交、未推送、未合并。
基线为 `dd6a0cd`。当前不是已完成交付，不要从通过的替身测试推断两类问题均已解决。

## 授权与约束

- 用户已明确同意实施 `docs/2026-11-01-caption-integrity-design.md`，恢复后无需重复请求实施许可。
- 不读取过期的 `PLAN.md`。本次计划是 `docs/superpowers/plans/2026-11-01-caption-integrity.md`。
- 不改动用户已有模型、音频、实验数据或服务。不要自动提交、推送、合并。
- 当前暂停优先于计划中的自动继续规则。

## 已保存的实现

- `audio/segmenter.py`：10 秒窗口上限，1.5 秒实际音频重叠；修正起始时间。
- `pipeline/utterances.py`：按源维护逻辑句 ID、跨窗尾句、稳定内句提交、30 秒/1000 字符预算；不确定对齐显式降级。
- `pipeline/orchestrator.py`：按源串行 ASR、在途音频/时间快照、冻结 MT 原文/历史/版本、流结束后的版本校验、finish 等待活动翻译、音频缓存补解码、背压。
- `audio/buffer.py`：有界音频缓存，缺口/过期区间不补解码。
- `audio/speech_evidence.py` / `asr/verification.py`：可选本地 Silero 旁路观测，记录在字幕/历史之前；默认关闭，不以低分拦截。
- `config.py` / `default.toml`：窗口、待完成句子、缓存、观测配置校验；补解码默认类型值 10 秒，本机 TOML 为已探测的 30 秒。
- `audio/capture.py`：设备/采样率重开后保留累计样本时间；新 start 重置。
- `cli.py`：观测初始化错误产生 session error 并正常返回。
- 清理旧 carry 私有实现；上下文测试迁移到实际新提交路径，旧配置字段尚待标明弃用。
- `tools/check.py` 与新增/迁移回归测试已保存。

## 验证证据

原始 25 个脚本基线通过。当前已扩至 34 个脚本。

最近一次全量结果为 **33 PASS / 1 FAIL / 0 SKIP**，唯一失败是 `test_window_pipeline.source_order` 的人工样本：
它要求在 0.2 秒重叠里对齐 9 个字母，不满足新时间区域约束。
已将该测试重叠调整为 0.5 秒；随后 `test_window_pipeline` 独立通过。
**调整后尚未重新跑全部 34 个脚本，不可表述为全量已通过。**

其他已验证：设备重开测试先红 `[.02,.02]` 后绿 `[.02,.04]`；观测启动错误先红后绿；
在途请求元数据快照、16 秒缓存补解码与 10 秒请求上限、旧 MT 版本、忙状态/淘汰/历史回归均单独通过。
utterance 模块共 17 项行为测试通过。

### 真实模型回放

旧版：`runs/caption-integrity/baseline/real-service/`。
首次新实现（已发现并修复重复回归）：`runs/caption-integrity/current-service/`。
最新完整回放：`runs/caption-integrity/current-service-v2/`。
每处有 manifest、原始 ASR、MT 请求/响应、事件、summary、日志。

- 25.02 秒人工重复样本：旧版 14 条且有孤立残句；首次新实现 7 条并含巨大重复尾句；修复后实际模型链路 **15 条**，五句完整重复三轮，均有译文。ASR 29 次、MT 43 次、墙钟 26.32 秒。
- 7.66 秒普通英文：保持三条定稿，ASR 9 次、MT 10 次、墙钟 7.73 秒。
- 12.70 秒困难日语：四条定稿，末句有合并和专名识别问题；尚无人工源文标注，不应声称质量改善。
- 12 秒无字幕候选背景：本次无字幕，旧版曾显示“嗯。”/“啊！”草稿。未人工核听，不能据此报告幻觉下降率。
- 该重复样本是 8.34 秒录音复制三次，**不是自然 25 秒长句**。真正自然长句的质量验收仍需数据。
- 30 秒模型容量探测实际 HTTP 200，不能据此保证所有 30 秒音频质量。
- 本地 Silero 对既有人声困难片段覆盖率只有 32.9%–80.2%；当前证据不支持自动否决。

重叠修复使用窗口文字密度与时长约束候选区域，修复了全文全局唯一性误拒绝；
这仍是启发式时间估计，无词级时间戳，极端变速/识别缺漏仍可能降级。

## 审查发现：恢复后的首要任务

审查报告：`runs/caption-integrity/integration-review.md`；复现：`runs/caption-integrity/integration-review-repro.py`。

1. **P1，尚未修复：最终 ASR 被 guard 全拒绝后，旧草稿被当成正常定稿并写入历史。**
   `_decode_window` 调用 `_ingest_text('', final=True)`，assembler 的空文本分支提交旧 pending。
   最终识别失败/空结果也需区分于可靠确认；当前 finish 无条件 flush 可能再次提交。
2. **P2，尚未修复：finish 超时后 stop 取消任务，没有明确的待完成句子终止/未确认诊断。**
   保留草稿可接受，但不可静默把它当已确认历史。

暂停前仅提出、**尚未写入**的修复方向：

- 扩展 `UtteranceAssembler.flush(source=None, *, final=True, reason='')`。
- `final=False` 输出同 ID、同原文的草稿和未确认原因，随后清理 pending，防止后续 flush 再变定稿。
- Orchestrator 记录最终失败/空结果/guard 拒绝的来源；正常 finish 对这些来源只作 `asr_unconfirmed` 非定稿结束，不入正式历史。
- finish 超时后的 stop 以 `stop_unconfirmed` 非定稿结束剩余 pending，并记录诊断。
- 需要先补可复现失败测试，再实现；低 Silero 分数仍不得触发此逻辑。

以上只是待实施方向，恢复后应根据审查完整报告再检查边界，不要误认为已实现。

## 后续收尾

1. 修复上述审查问题并回归；完整检查串行、结束、内存、UI ID、失败行为。
2. 完整运行 `.venv/Scripts/python.exe tools/check.py`（本会话 Python 启动需 approved escalation）。
3. 最后做一次包含 untracked 新文件的全量代码审查；必要时针对行为变化复跑真实模型。
4. 更新 `docs/current-state.md` 正文、设计状态及计划完成项；当前正文仍是基线说明。
5. 幻觉部分目前只交付旁路观测，没有可启用的自动过滤；仍需校准/独立验证集和真实对白漏失核查。

## 临时服务与进程

本次启动的 ASR/MT 记录在 `runs/caption-integrity/validation-services/launches.json`。
下班清理仅允许停止 PID、启动时间、可执行路径全部相符的本次进程：
ASR 41356、MT 28128。清理结果应查看同目录 `cleanup.json`，不可仅凭 PID 停止进程。
所有回放与测试会话均已结束；无后台回放需继续运行。
