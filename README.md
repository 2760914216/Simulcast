# Simulcast · 同声传译

Windows 桌面实时字幕工具：采集系统声音或麦克风，通过 ASR 识别，再翻译成中文字幕。
默认使用本机 Qwen3-ASR 与 Qwen3.5-9B，字幕由独立的 PySide6 悬浮窗显示。

支持本地离线运行，也可把翻译切换到兼容 Chat Completions 的云端服务。
ASR 当前使用 llama.cpp 风格的音频 Chat Completions 接口，需要支持该输入格式的服务。

详细架构、当前行为与局限见 [现状说明](docs/current-state.md)。

## 当前能力

- 系统内录、麦克风或双路采集；默认跟随 Windows 默认设备，也可在托盘指定设备。
- ASR 草稿持续更新；首轮译文流式显示，已有译文后的重译先缓冲、完成后一次替换，避免反复退回开头几个字。
- 10 秒 ASR 窗口与逻辑句子分离，硬切保留 1.5 秒重叠，待完成句子跨窗沿用同一字幕 ID。
- 翻译参考前两句已提交的原文；入队时冻结原文、上下文和版本，过时翻译不覆盖新版本。
- 置顶双语悬浮窗、点击穿透、圆形拖拽点；可调字号、宽度、历史句数及原文显示。
- 托盘管理识别与字幕进程，支持重启、停止、单实例及崩溃重试。
- WAV 回放、JSONL 事件记录、双语稿与 SRT 导出、断句评估。
- 可选本地 Silero 语音证据观测，默认关闭；当前不根据观测分数自动过滤字幕。

## 快速开始

### 环境

需要 Windows 10/11、Python 3.11 及以上版本（本机测试使用 3.13），以及支持所选模型的推理服务。
本地模型所需显存取决于量化、上下文长度和服务配置；模型文件需自行准备。

在仓库根目录用 PowerShell 执行：

```powershell
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt
```

### 启动模型服务

应用管理采集和字幕进程，模型服务需要单独启动。
默认地址为 ASR `http://127.0.0.1:18091`、MT `http://127.0.0.1:18092`。
以下为 llama.cpp 示例，模型路径与参数请按本机版本调整：

```powershell
llama-server -m Qwen3-ASR-1.7B-Q8_0.gguf `
  -mm Qwen3-ASR-1.7B-mmproj-f16.gguf `
  --host 127.0.0.1 --port 18091 -ngl 99 -c 4096 --jinja

llama-server -m Qwen3.5-9B-Q4_K_M.gguf `
  --host 127.0.0.1 --port 18092 -ngl 99 -c 4096 --jinja -fa on
```

两条命令分别运行在两个终端中。模型名称、端口等配置见 `config/default.toml`。

### 运行

```powershell
.\run.bat
```

托盘中可切换内录、麦克风和双路采集，以及调整字幕样式。
也可在两个终端分别启动识别与字幕引擎：

```powershell
& .venv/Scripts/python.exe -m simulcast.cli run --live loopback --ws
& .venv/Scripts/python.exe -m simulcast.render.qt --ws ws://127.0.0.1:8765
```

字幕端可先启动，连接断开时会重连；`--demo` 可在没有识别服务时预览样式。

## 配置

```text
config/
  default.toml           默认参数与服务定义
  profiles/local.toml    本地 ASR + 本地翻译
  profiles/cloud-mt.toml 本地 ASR + DeepSeek 翻译
  render.toml            字幕出厂样式
```

配置按 `default.toml` → `profiles/<name>.toml` 深合并。
运行时字幕样式保存在 `%APPDATA%\simulcast\render.json`，覆盖出厂样式。
密钥只通过配置指定的环境变量读取。

```powershell
# 可选云端翻译：先在当前环境设置 DEEPSEEK_API_KEY
& .venv/Scripts/python.exe -m simulcast.cli run --live loopback --profile cloud-mt --ws
```

主要参数：

| 配置 | 默认值 | 用途 |
|---|---|---|
| `segment.draft_interval_ms` | 900 | 草稿识别触发间隔 |
| `segment.silence_ms` | 450 | VAD 静音判停时长 |
| `segment.max_segment_s` | 10 | 单个常规 ASR 音频窗口上限 |
| `segment.window_overlap_s` | 1.5 | 硬切后的重叠音频 |
| `segment.utterance_max_s` / `utterance_max_chars` | 30 / 1000 | 待完成句子的预算 |
| `mt.context_sentences` / `context_max_chars` | 2 / 2000 | 翻译原文上下文预算；任一为 0 关闭 |
| `asr.speech_evidence_mode` | `off` | 设为 `observe` 可开启语音证据旁路 |

语音证据观测另需安装 `onnxruntime`，并配置本地 Silero ONNX 模型路径
`asr.speech_evidence_model`。常规运行无需这些可选依赖。

## 数据流与字幕协议

```text
采集 → 能量 VAD → 重叠音频窗口 → 同源串行 ASR → 逻辑句子 → 翻译队列
                                                              ↓
                      PySide6 悬浮窗 ← WebSocket ← 字幕事件 → JSONL
```

总管 `simulcast.app` 管理识别引擎 `simulcast.cli` 与字幕引擎 `simulcast.render.qt`。
Windows Job Object 绑定子进程生命周期，避免总管退出后留下引擎进程。

WebSocket 默认地址为 `ws://127.0.0.1:8765`。客户端按字幕 `id` 更新内容，连接时接收快照：

```json
{"v":1,"type":"caption","id":"u1","source":"loopback","t0":3.1,"t1":null,"raw":"Today we discuss…","translation":"今天我们讨论……","state":"draft","lang":"English","asr_ms":168,"mt_ms":192,"seq":41,"sid":"session-id"}
```

`u1`、`u2` 等是会话内逻辑句子的 ID，跨音频窗口保持不变。
`draft` 可修订，`final` 表示已确认结束；`t1=null` 表示尚未定稿。
`utterance_boundary`、`asr_repair`、`asr_observation` 等事件用于诊断，不作为字幕显示。

## 回放与导出

```powershell
& .venv/Scripts/python.exe -m simulcast.cli devices
& .venv/Scripts/python.exe -m simulcast.cli run --input a.wav --speed 1 --out a.jsonl
& .venv/Scripts/python.exe -m simulcast.cli export a.jsonl --list
& .venv/Scripts/python.exe -m simulcast.cli export a.jsonl -o a.md
& .venv/Scripts/python.exe -m simulcast.cli export a.jsonl --format srt -o a.srt
& .venv/Scripts/python.exe -m simulcast.cli eval a.jsonl --worst 5
```

JSONL 默认追加写入；使用 `--fresh` 清空后重写。导出默认仅包含定稿，`--all` 可包含草稿。

## 测试

```powershell
& .venv/Scripts/python.exe tools/check.py
# 仅运行指定测试脚本
& .venv/Scripts/python.exe tools/check.py test_mt_display test_pipeline_shutdown
```

测试入口为每个脚本建立独立 Python 进程，使用离屏 Qt，并设置超时。
2026-11-04 本机完整检查为 **37 个脚本通过，0 失败、0 跳过**。
这些回归不依赖真实模型服务和音频设备，不代表真实长句翻译与背景音幻觉已完成验收。

## 已知限制

- 跨窗文字对齐依赖启发式规则，歧义、模型漏字和预算耗尽仍可能导致重复或分句。
  自然连续长句还缺少人工标注实录验收。
- 背景音幻觉尚未解决。已有 ASR 退化守门与可选语音证据观测，尚无经过独立验证的声学自动拦截策略。
- ASR 无词级时间戳，句子时间按文字比例估计；停顿与标点不能保证语义句界。
- 后续重译需要等该次翻译完成才替换显示；慢服务会延迟更新，翻译队列在持续过载下仍可能增长。
- 双路采集的句子与音频状态独立，但共用翻译队列和已提交原文历史，可能互相排队、影响上下文。
- 仅实现 `openai-chat` 适配器；不同服务的音频输入、健康检查和扩展参数需要核对兼容性。
- 音频采集只支持 Windows；没有 TTS、虚拟麦克风、说话人分离和多屏同时显示。

## 仓库目录

```text
simulcast/  采集、ASR、翻译、管道、字幕渲染与托盘总管
config/     默认配置与服务组合
docs/       当前代码的说明
test/       单元与集成回归
tools/      测试入口与设备工具
run.bat     桌面启动入口
LICENSE     MIT 许可证
```

设计、交接、实现计划、实验和验证记录保存在本机 `.ignore/development-docs/`；
原根目录的 `TODO.md` 也已移入该目录。`docs/superpower/`、`docs/superpowers/`、
重新创建的根目录 `TODO.md`、模型、音频样本和运行产物均不入库。

## 许可

本仓库代码采用 [MIT 许可证](LICENSE)，允许商用、修改、再分发与再许可，需保留版权和许可声明。
模型权重、推理服务及第三方依赖适用各自许可，不包含在本项目的 MIT 授权中。
例如 PySide6/Qt 与 soxr 含 LGPL 等许可要求；分发包含这些组件的程序时需遵守相应条款。
