# Simulcast · 同声传译
- 这写的很 deepseek (吐槽)

Windows 桌面端、本地优先的实时同声传译：把系统内录（或麦克风）里的英/日语，边说边变成中文字幕。

识别和翻译都走 **OpenAI 兼容的 HTTP 接口**——默认接本机 llama.cpp，也可以换成任何兼容端点（ollama、DeepSeek、OpenAI…）。字幕渲染是**独立进程**，只通过 WebSocket 协议通信，换 UI 不用碰管道。

- ASR：Qwen3-ASR（llama.cpp，支持音频输入）
- MT：任意 chat 模型（默认 Qwen3.5-9B Q4）
- 字幕：PySide6 悬浮窗，圆角卡片、点击穿透、可拖拽、双语 + 上一句

## 特性

- **全本地可跑**：录音、识别、翻译、字幕都在本机；不需要联网。
- **草稿–定稿双轨**：ASR 还在说话时就出草稿译文，判停后整段重解码定稿，字幕不会突然"跳"。
- **句子级切分**：按标点切句，连续两次解码一致才提交；长句跨段接力，硬切不切在词中间。
- **译文位只放译文**：新句译文到达前保留上一条，最多 1.2 s，避免中英来回闪。
- **稳定前缀着色**：同一句里已确认的部分保持亮色，未稳定的尾巴用灰色，眼睛只需要盯句尾。
- **可调外观**：字号、宽度、位置、历史句数、主题色；拖拽点可移动、可换大小。
- **可观测**：每条字幕带 ASR/MT 耗时；`eval` 命令给出"切早了"的量化指标。

## 架构

```
WASAPI loopback ┐
                ├─► 16 kHz ─► VAD/分段 ─► ASR(draft/final) ─► 句子提交 ─► 翻译 ─► 字幕事件
麦克风          ┘   soxr        burst       HTTP                LocalAgreement   HTTP      │
                                                                                          ▼
                                                              WebSocket 协议 ──► 渲染端（PySide6）
                                                                              └─► JSONL / SRT / MD
```

## 快速开始

### 1. 环境

- Windows 10/11（音频采集依赖 WASAPI）
- Python 3.13
- 一个 OpenAI 兼容的 LLM 服务（下例用 llama.cpp）

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
```

### 2. 启动模型服务

应用**不负责**拉起进程，只要端点可达。两个本地服务示例：

```bash
llama-server -m <models>/Qwen3-ASR-GGUF/Qwen3-ASR-1.7B-Q8_0.gguf \
             -mm <models>/Qwen3-ASR-GGUF/Qwen3-ASR-1.7B-mmproj-f16.gguf \
             --host 127.0.0.1 --port 8091 -ngl 99 -c 4096 --jinja

llama-server -m <models>/Qwen3.5/gguf/Qwen3.5-9B-Q4_K_M.gguf \
             --host 127.0.0.1 --port 8092 -ngl 99 -c 4096 --jinja -fa on
```

本机可以写一个 `scripts/serve-local.sh`（仓库已 gitignore）把两条命令和 `trap kill` 放在一起，按需修改端口和模型路径。

### 3. 运行

```bat
run.bat            :: 内录 + 悬浮字幕
run.bat mic        :: 麦克风
run.bat both       :: 两路同时
```

或直接命令行：

```bash
PYTHONPATH=. .venv/Scripts/python.exe -m simulcast.cli run --live loopback
PYTHONPATH=. .venv/Scripts/python.exe -m simulcast.render.qt --ws ws://127.0.0.1:8765
```

渲染端也可以先起，会自动重连。没有服务端时用 `--demo` 预览样式。

## 配置

```
config/
├─ default.toml          # provider 定义与默认参数，不含机器路径/密钥
├─ profiles/
│  ├─ local.toml         # 默认：本地 ASR + 本地 MT
│  └─ cloud-mt.toml      # 本地 ASR + DeepSeek 翻译
└─ render.toml           # 渲染端出厂默认（窗口 + 主题）
```

加载顺序：`default.toml` → `profiles/<name>.toml`（深合并，只写差异）。渲染端的托盘设置写在
`%APPDATA%\simulcast\render.json`，覆盖 `render.toml`，仓库里的默认值不会被改写。

### provider

```toml
[asr]
provider = "qwen3-local"

[asr.providers.qwen3-local]
type = "openai-chat"          # 目前只实现这一种
base_url = "http://127.0.0.1:8091"
model = "qwen3-asr"
max_tokens = 512
timeout_s = 30
# api_key_env = "DASHSCOPE_API_KEY"   # 云端才需要，这里只写变量名
```

`[mt]` 结构相同，额外有 `target_lang`、`context_sentences`、`emit_interval_ms`；provider 里多
`draft_max_tokens`、`thinking`。

- 换模型/端点：只改 `model` / `base_url`；
- 换整套组合：新增 `profiles/*.toml`；
- 密钥只走环境变量，缺失时启动即报错。

```bash
python -m simulcast.cli run --live loopback --profile local
python -m simulcast.cli run --live loopback --profile cloud-mt    # 需要 DEEPSEEK_API_KEY
```

## 字幕协议 v1

WebSocket `ws://127.0.0.1:8765`，每帧一个 UTF-8 JSON，**按 id upsert**，渲染端无状态可重连。

```json
{"v":1,"type":"snapshot","captions":[...]}
{"v":1,"type":"caption","id":"loopback-0003-s2","source":"loopback",
 "t0":3.1,"t1":5.4,"raw":"Today, we will discuss…","translation":"今天，我们将讨论……",
 "state":"final","lang":"English","asr_ms":168,"mt_ms":192,"seq":41,"sid":"20261017T…"}
```

| 字段 | 含义 |
|---|---|
| `sid` | 一次 `run` 的会话 ID |
| `id` | `loopback-0003` 是 burst 草稿行，`loopback-0003-s2` 是该 burst 的第 2 句定稿 |
| `t0` / `t1` | 音频时间轴上的起止；`t1=null` 表示还在增长 |
| `raw` / `translation` | ASR 文本 / 译文 |
| `state` | `draft` 边说边译，`final` 该句定稿 |
| `asr_ms` / `mt_ms` | 本次请求耗时 |

## 断句模型

1. **burst**：VAD 判停（450 ms 静音）或超过 10 s 才结束一段；≤450 ms 的换气不会断句。
2. **句子提交**：每次解码按标点切句，某句连续两次解码一致（LocalAgreement-2）才成为 `final`；
   当前正在说的那句留在 `draft` 上滚动。tick 阶段**永不提交最后一句**，避免把截断的句子定稿。
3. **跨段接力**：硬切时量切点静音长度——≥120 ms 视为真停顿；否则把这半句剥掉尾标点后挂起，
   拼到下一段识别结果前。挂起文本 20 s 过期、12–200 字符、≤8 词（或以上下文连接词结尾）。
4. **保真优先**：`segment.semantic_heuristics = false`（默认）时只做机械处理，不在文本层猜语义。

`eval` 会把"在线规则已提交的句子"和"整段全文重切的句子"对照，输出 `exact-match` 和
`char premature`，用来判断切早了还是切晚了。

## 渲染端

- 近黑半透明卡片、圆角、左侧连接状态点（白=已连接，灰=断开）。
- 当前句：原文小字 + 译文大字；可选择保留 0/1/2 句历史（只留译文，单行省略）。
- 稳定前缀亮色、未定尾巴灰色；180 ms 合并窗口，final 立即生效。
- 卡片点击穿透；右侧圆形拖拽点可移动位置，位置按"横向比例 + 底部距离"保存。
- 托盘：字号、宽度、历史句数、拖拽点大小、显示原文、退出；单击托盘图标显示/隐藏。

## CLI

```bash
python -m simulcast.cli devices                      # 列出内录/麦克风
python -m simulcast.cli run --live both --ws          # 双路 + 字幕总线
python -m simulcast.cli run --input a.wav --speed 1   # 实时回放 wav
python -m simulcast.cli serve                         # 只跑字幕总线
python -m simulcast.cli export a.jsonl --list         # 查看文件里的会话
python -m simulcast.cli export a.jsonl -o a.md        # 折叠成双语稿
python -m simulcast.cli export a.jsonl --format srt   # 导出字幕
python -m simulcast.cli eval a.jsonl --worst 5        # 断句质量报告
```

常用参数：`--profile`、`--out`（追加写，`--fresh` 截断）、`--no-mt`、`--max-seconds`、`--quiet`。

## 实测

RTX 5070 Ti Laptop（12 GB）上，两个本地服务同时常驻约 11.5 GB：

| 指标 | 值 |
|---|---|
| ASR 单次解码（1–7 s 音频） | 85–340 ms |
| MT 单句（含首 token） | 80–280 ms |
| 说话中首条译文 | 1.2–1.5 s |
| 停顿后定稿替换 | 0.2–0.5 s |

## 测试

```bash
for t in test/test_config.py test/test_pipeline.py test/test_carry.py test/test_commit.py \
         test/test_eval.py test/test_heuristics.py test/test_render_model.py \
         test/test_render_behavior.py test/test_render_history.py test/test_render_interaction.py; do
  PYTHONPATH=. .venv/Scripts/python.exe "$t" || break
done
```

覆盖：配置合并/provider 解析、断句与 carried 文本、提交对齐、eval 指标、渲染端 store/拖拽/主题/历史。
仓库只保留不依赖音频设备/模型服务的单元测试；需要真实服务或显示器的脚本（probe/capture/live）留在本机，不入库。

## 已知限制

- ASR 不返回词级时间戳，句子时间是按字符比例估算的（srt 可用，逐字高亮不够）。
- 句界依赖 ASR 标点；长句硬切后若尾巴较长，仍可能被切成两句。
- 只实现了 `openai-chat` 这一种方言；Qwen-MT（`translation_options`）和 DashScope ASR（multimodal）
  需要各自的适配器。
- 只有 Windows 音频采集；没有 TTS、虚拟麦克风、说话人分离。
- 渲染端字幕窗固定置顶，不支持多屏同时显示。

## 目录

```
simulcast/
  audio/     devices capture vad segmenter wavio
  asr/       base（协议 + Streaming 扩展点） openai_chat
  mt/        base openai_chat prompts sanitize
  pipeline/  sentences orchestrator（双轨调度、背压、上下文）
  render/    client model textblock overlay handle theme qt cli
  protocol.py hub.py sink.py sources.py cli.py
config/      default.toml profiles/ render.toml
test/        单元与集成测试
run.bat devices.bat
```
