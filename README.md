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

运行期间，本地 WebSocket 事件流在 `ws://127.0.0.1:8765`，每条消息是一个 JSON 事件。

## 测试

```sh
uv run pytest
```

测试语料由 macOS 自带的语音合成在测试时生成。

## 协议

Apache License 2.0，第三方声明见 `NOTICE`。
