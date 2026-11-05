# GivingEar

给 AI 一只耳朵：在多人语音对话中分辨是谁在讲话。

几个人在同一个房间、共用一个麦克风和 AI 聊天时，GivingEar 在本地用声纹分辨每句话是谁说的，并把"[现在说话的是：小王]"这样的说话人标签实时告诉 AI。

> 开发中。设计见 `.scratch/givingear-v1/spec.md`，任务见 `.scratch/givingear-v1/issues/`。

## 运行（目前仅支持 macOS）

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
