# GivingEar 做成 Agent Skill：让 Claude 知道房间里谁说了什么

Status: in-progress

## Problem Statement

Claude Code、Claude Desktop 和其他 AI 助手都"听不见"线下的多人对话。现有工具（2026-11 调研）分两类：
- 会议产品（Otter、Fireflies、Granola 等）：要接入线上会议才能用，而且都是开完会才有转写。
- 开源 MCP：要么需要 NVIDIA 显卡（snailbrainx），要么实时模式只能分"我 / 他们"（whosaid），要么没有声纹注册、不支持中文。

**目前还没有一个 skill 能做到：在 Mac 上用房间里的一个麦克风实时收音，按人转写（认得出注册过的人，中英文都支持），再让 Claude 回答"谁说了什么"。**

## Solution

GivingEar 增加"只当耳朵"的模式：只录音、分辨说话人、在本地转写，不接任何 AI 模型，由调用它的 AI 助手充当大脑。对外提供三种用法：

1. **命令行**（任何能运行 shell 命令的 AI 助手都能用）：
   - `givingear listen start|stop|status`：开始 / 停止 / 查看后台收音
   - `givingear transcript`：按说话人输出对话记录
   - `givingear who`、`givingear name`：查看房间里的人、给人命名
   - `givingear diarize 文件`：给录音文件按说话人转写
2. **两个 skill**（Agent Skills 规范，放在 `skills/` 下，`npx skills add youning62/Giving-Ear` 就能安装）：
   - `speaker-identification`：实时听房间里的对话，回答谁说了什么
   - `speaker-diarization`：给录音文件按说话人转写
3. **MCP 服务器 + Claude Code 插件**：仓库本身就是插件市场，Claude Desktop 和 Claude Code 可以直接调用工具。

## Implementation Decisions

- **后台进程：** `listen start` 启动一个后台进程，负责采集麦克风音频，运行会话协调器（接一个空的语音模型后端），把带说话人的转写保存在**内存**里。
  - 对外只在本机 127.0.0.1 的随机端口提供 HTTP 接口，必须带随机令牌才能访问。
  - 端口和令牌写在 `~/.givingear/listen.json`，权限 0600，只有用户本人能读。
- **隐私：**
  - 依然不保存原始音频，转写只在内存里。
  - `listen stop` 时转写随之丢弃；只有用户明确要求时，才用 `--save 文件` 保存成文字。
  - 默认最长收音 4 小时，到时自动停止。
  - skill 要求 AI 提醒用户告知房间里的人"正在记录"。
- **说话人名字以当前为准：** 转写记录保存的是说话人编号，查询时再换成当前的名字。所以给某人命名或登记后，之前的历史记录也会显示新名字。
- **声纹库：** `listen start --account 名字` 会解锁账号里已登记的声纹。密码只能在交互式终端里输入，或者通过标准输入传给后台进程，不出现在命令参数里。AI 助手启动时不带账号，名字只在本次会话有效。
- **录音文件：** `diarize` 在当前进程里以最快速度处理文件，输出格式和 `transcript` 相同。
- **输出格式：** 默认是文字（`[时:分:秒] 名字: 内容`），加 `--json` 输出结构化数据。
- **打包：**
  - 发布到 PyPI，包名 `givingear`，版本 0.2.0。
  - pyobjc 只在 macOS 上安装，其他平台也能用 `diarize`。
  - 插件的 MCP 用 `uvx givingear==<精确版本> mcp` 启动。
- **发现渠道：**
  - skill 名字直接用搜索词，描述写中英双语关键词。
  - GitHub topics、README 写好关键词和安装命令。
  - 各目录逐一提交，每次对外提交都要先经用户同意。

## Testing Decisions

- 继续用核心测试接口：WAV 输入加空后端。
- 转写记录模块单独测试：改名后历史记录跟着变、按时间和说话人过滤。
- 后台进程的 HTTP 接口：测试中用 WAV 音频源代替麦克风，测试启动、查询、命名、停止，以及没有令牌时被拒绝。
- `diarize` 在合成语料上测试说话人数量和转写内容。
- MCP 工具：测试工具列表和调用结果，直接调用工具函数，不走 stdio。

## Out of Scope

- 实时语音回应（那是 GivingEar 应用本身的事）
- 手机和云端
- 在 Windows / Linux 上实时收音
- 把重叠的声音分离开
