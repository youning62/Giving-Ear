# GivingEar

给 AI 一只耳朵：在多人语音对话中分辨是谁在讲话。

几个人在同一个房间、共用一个麦克风和 AI 聊天时，GivingEar 在本地用声纹分辨每句话是谁说的，并把"[现在说话的是：小王]"这样的说话人标签实时告诉 AI。

> 开发中。设计见 `.scratch/givingear-v1/spec.md`，任务见 `.scratch/givingear-v1/issues/`。

## 最简单的用法（目前仅支持 macOS）

双击 `GivingEar.command`（或运行 `uv sync && uv run givingear`），浏览器会自动打开页面。之后所有操作都在页面里完成：在"设置"里填 Gemini API 密钥，在"账号"里注册并登录，点"开始监听"，在人名卡片上点"登记声纹"。第一次开始监听时，macOS 会询问麦克风权限。

## 选择模型（国内可用）

设置里可以选三种模型：

- **Google Gemini**：原生语音，延迟最低，但在中国大陆通常无法直接连接。
- **DeepSeek**：文字模型。你说的话先在本机转成文字（带说话人名字），发给 DeepSeek，回复再由本机合成语音播放。国内可直接使用。
- **自定义（OpenAI 兼容接口）**：通义、智谱、月之暗面等兼容 OpenAI 接口的模型，填接口地址和模型名即可。

设置里的"测试连接"可以确认密钥和网络是否正常。文字模型的取舍：延迟比 Gemini 高，模型听不到语气。

### 让 AI 的声音有情感

文字模型默认配的是 macOS 自带的声音，情感很弱。想要更自然的情感，需要两件事一起做（程序已经替你做好了）：文字模型给每句话标一个情绪（开心、温柔、难过……），再由支持情绪的语音引擎来朗读。在设置的"AI 的声音"里选：

- **通义千问云端（推荐）**：国内直连，用自然语言指令控制语气，手机上也能用；需要 [阿里云百炼](https://bailian.console.aliyun.com/) 的 DashScope 密钥，每句话多一次网络往返。
- **本机自带**：离线、免费。靠语速、音高、音量表现情绪（开心更快更高，难过更慢更低更轻），效果有限，仍然是机器的声音。
- **本地 CosyVoice**：免费、声音不出本机，但只能在电脑上用，需要自己部署它的服务（[CosyVoice](https://github.com/FunAudioLLM/CosyVoice)，Apache-2.0）。在它的 `runtime/python/fastapi` 目录运行 `python3 server.py --port 50000 --model_dir iic/CosyVoice2-0.5B`，再在设置里填服务地址（默认 `http://127.0.0.1:50000`）和一段参考录音（WAV）。

设置里有"试听"：选一个情绪，当场听效果。云端和 CosyVoice 的实际效果、延迟依赖你的网络和机器，建议先试听再用。

第一次使用要下载几个本地模型文件（来自 GitHub）。如果国内网络下载很慢或失败，可以设置镜像前缀后再启动：`GIVINGEAR_DOWNLOAD_MIRROR=镜像前缀 uv run givingear`，或者手动下载文件放到 `~/.cache/givingear/models/`（报错信息里会写明文件名和地址）。

## 命令行用法

```sh
uv sync
uv run givingear --wav 对话.wav            # 用 WAV 文件代替麦克风
uv run givingear --wav 对话.wav --realtime # 按真实速度
```

加上 `--web` 会在 `http://127.0.0.1:8766` 打开本地可视化网页：房间里的人、对话、谁在说话的时间线、AI 收到的名单和事件流。页面右上角的"演示"按钮不需要麦克风，可以直接预览效果。

```sh
uv run givingear --web
```

## 账号与声纹库

声纹登记在账号里，加密保存在这台电脑的 `~/.givingear/`（可用 `GIVINGEAR_HOME` 改位置）。忘记密码就无法找回里面的声纹。

```sh
uv run givingear account create wang           # 创建账号（会提示设置密码）
uv run givingear --account wang --web           # 用账号登录并开始听；对 AI 说"帮我注册"就能登记声纹
uv run givingear speakers list                  # 查看已登记的人
uv run givingear speakers delete 小王           # 让程序彻底忘记一个人
uv run givingear account passwd wang            # 改密码
```

网页右上角的"账号"可以登录、查看和删除已登记的声纹。

运行期间，本地 WebSocket 事件流在 `ws://127.0.0.1:8765`，每条消息是一个 JSON 事件。

## 测试

```sh
uv run pytest
```

测试语料由 macOS 自带的语音合成在测试时生成。

## 协议

Apache License 2.0，第三方声明见 `NOTICE`。
