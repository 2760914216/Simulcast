# 现状

> 描述截至 `90a1b7a` 的实际行为，拿来替代已经作废的 `PLAN.md`（已挪进 `.ignore/`）。
> 想做但还没做的记在 `TODO.md`（本机文件，不入库）。

## 一句话

Windows 上把系统内录或麦克风的音频，实时转成中文双语字幕。识别和翻译都跑本机
`llama-server`，字幕由独立的置顶悬浮窗渲染，三者之间用 WebSocket 协议解耦。

## 现在能做什么

| 能力 | 状态 | 说明 |
|---|---|---|
| 系统内录 | 可用 | 默认跟随 Windows 默认输出设备，换耳机/切显示器自动重开流 |
| 麦克风 | 可用 | 同上，也可在托盘里把设备钉死 |
| 双路同时 | 可用 | 两路各自成会话，但共用同一个 Orchestrator 与翻译队列（见局限） |
| VAD 断句 | 可用 | 450 ms 静音判停，超过 10 s 强制切；硬切时把没说完的半句接力到下一段 |
| ASR | 可用 | Qwen3-ASR 1.7B（GGUF，Q8_0 + mmproj），带幻读/复读守门 |
| 翻译 | 可用 | 默认 Qwen3.5-9B 本地；`--profile cloud-mt` 换 DeepSeek，接口同一种方言 |
| 流式字幕 | 可用 | 草稿边出边译，句末定稿替换；翻译请求带前 x 句原文作为参考 |
| 悬浮窗 | 可用 | 置顶、点击穿透、可拖动的圆形拖拽点、原文小字 + 译文大字 |
| 外观调节 | 可用 | 字号 / 宽度 / 保留历史行数 / 拖拽点大小 / 显示原文，改动落盘 |
| 进程管理 | 可用 | 托盘总管拉起两个引擎，可重启/停止，崩溃自动重试 3 次后标红 |
| 单实例 | 可用 | 总管和字幕端各自互斥，第二次启动只唤出已有实例 |
| 离线回放 | 可用 | `--input a.wav --speed N` 按时间轴回放 |
| 记录与导出 | 可用 | `--out x.jsonl` 落盘；`eval` 出分段质量报告，`export` 转 md/srt |

## 架构

### 三个进程

```
simulcast.app                总管：托盘、单实例、子进程生命周期（无音频、无模型）
 ├─ simulcast.cli run       识别引擎：采集 → 断句 → ASR → 翻译 → 发字幕
 └─ simulcast.render.qt     字幕引擎：只连 websocket，渲染悬浮窗
```

- 总管用 **Job Object**（`KILL_ON_JOB_CLOSE`）绑住两个子进程，总管被强杀也不留孤儿。
- 子进程输出重定向到 `logs/pipeline.log`、`logs/render.log`，总管自己写 `logs/app.log`。
- 崩溃策略：连续启动失败 3 次停下并标红；连续跑满 20 s 后计数清零。

### 数据流

```
音频帧 → EnergyVad → Segmenter(burst/tick/end) → ASR(草稿+定稿) → 句子提交
      → MT 队列（定稿优先）→ 流式译文 → Hub → websocket → 悬浮窗
```

### 协议 v1

`ws://127.0.0.1:8765`，每帧一个 UTF-8 JSON，**按 id upsert**，客户端无状态可重连。

- `caption`：`id / source / t0 / t1 / raw / translation / state / lang / asr_ms / mt_ms / seq / sid`
- `snapshot`：连上时补发最近 120 条，解决中途接入
- `session`：`running / stopped / error`
- `eval`：分段质量指标（只落 jsonl，不上屏幕）
- `control`：`target=render|pipeline`，由 Hub 广播并回调管道，例如 `render.style`、`capture.device`

## 关键实现决定

**断句与提交**：一段音频（burst）结束才切段；每次解码按标点切句，同一句连续两次解码一致
（LocalAgreement-2）才定稿，正在说的那句留在 draft 上滚动。tick 阶段永不提交最后一句，
避免把截断的句子当完整句。硬切时用切点静音长度判断真假停顿，假停顿把半句剥掉尾标点挂起，
拼到下一段的识别结果前（20 s 过期，12–200 字符，≤8 词）。

**ASR 守门**：解码结果先过 `asr/guard.py` 再进管道——末段周期性复读按周期切掉，4-gram 多样性
过低视为退化整段丢弃，语言与配置不符丢弃，字符/秒超阈值丢弃。ASR 端 `repeat_penalty=1.05`
是实测值（1.1 会在歌词段自造续写）。`asr.guard=false` 可整开关掉。

**翻译上下文（v1）**：只给**已提交的 ASR 原文**，不给历史译文，也不给当前句的上一个译文版本。
两道预算 `mt.context_sentences`（默认 2 句）与 `mt.context_max_chars`（默认 2000 字符），
任一为 0 关闭，负值启动即报错。最近一句自己就超字符上限时历史为空——不截断、也不跳过它。
原文一定稿就进历史（与翻译成败无关），快照在入队时冻结，晚处理的句子看不到排在它后面的原文。
请求形态是 `system` + 单条 `user`，用 `【前文】`/`【本次原文】` 分区，不构造 assistant 历史译文轮次。

**采集跟随**：采集线程每 1 s 比对 Windows 默认端点，变了就重开流；用户显式选的设备优先于自动跟随。
切换丢 0.3–1 s 音频，时间轴按采样累计，不插静音，也不主动 flush 当前句。

**渲染端无状态**：字幕端不存会话，只按 id upsert；外观状态由自己落盘到
`%APPDATA%\simulcast\render.json`，总管的托盘菜单读同一个文件来显示勾选。

## 局限

- 双路（内录 + 麦克风）共用一个 Orchestrator 和一个翻译队列：历史会互相串，两路会互相排队。
- ASR 不给词级时间戳，句子时间是按字符比例估的；srt 能用，逐字高亮不够。
- 句界依赖 ASR 标点；长句硬切后尾巴偏长时仍可能被切成两句。
- 只实现 `openai-chat` 一种方言；Qwen-MT（`translation_options`）、DashScope multimodal 都要新适配器。
- 只有 Windows 音频采集；没有 TTS、虚拟麦克风、说话人分离。
- 字幕窗固定置顶，不支持多屏。
- 日语动漫效果未系统验证（英语路径验证充分一些）。
- 上下文里的历史只到"前 x 句原文"：术语表、场景预设、滚动摘要都还没做。

## 开发方式

### 环境

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
```

### 模型服务

应用**不负责**启动服务，需手动开两个 `llama-server`（ASR 18091、MT 18092）。
本机脚本 `scripts/serve-local.ps1` 只用于自己电脑，被 `.gitignore` 挡住，不进仓库。

### 运行

```bat
run.bat          :: 总管（托盘 + 两个引擎）
tools\devices.bat :: 列出采集设备
```

### 测试

```bash
for t in test/test_*.py; do PYTHONPATH=. .venv/Scripts/python.exe "$t" || break; done
```

仓库里只留不依赖音频设备/模型服务/显示器的测试；需要真实服务的脚本留在本机
（`test/test_capture.py`、`test/test_qt.py`、`test/test_render_live.py`、`test/test_render_shot.py`），
音频样本放 `test/fixtures/`（本地生成，同样不入库）。

### 本机专属文件

`.ignore/` 草稿与作废文档、`TODO.md`、`AGENTS.md`、`scripts/serve-local.ps1`、
`tools/` 里的样本制作脚本、`runs/` 运行产物、`logs/` 日志、`vad_lab/` VAD 实验。

## 演进记录

| 提交 | 内容 |
|---|---|
| `1b5487f` | 初版：ASR + 翻译 + 渲染三件套，本地 Qwen 打通 |
| `44480d8` | 文档：服务启动方式不进仓库，改成本机脚本 |
| `a40638b` | 采集跟随 Windows 默认输出设备，可按设备钉死 |
| `ba80481` | 托盘升级成总管，管理两个引擎的生命周期；Job Object + 单实例 |
| `56c4665` `b103cc8` `9b92723` | 修 ASR 侧：只有界面上确实定稿的字幕进"上一句"、解码未回不允许淘汰、`asr_busy` 覆盖整个 ingest |
| `67894ad` `c14122f` | ASR 幻读守门；`repeat_penalty` 按实测调到 1.05 |
| `90a1b7a` | 翻译上下文 v1：前 x 句已提交原文，双预算，快照入队即冻结 |

VAD 的选型实验记录保留在 [`vad-silero-study.md`](vad-silero-study.md)（结论：暂不上 silero）。

## 未完成

见 `TODO.md`：整段 ASR → ASS 的离线模式、歌曲模式、日语动漫优化、上下文分类与开关、
术语表、滚动摘要、设置窗口等。
