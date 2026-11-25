# 0002: 国内模型用"文字模型 + 本机语音"流水线，与 Gemini 并存

**Status:** accepted（2026-11-04）

**Context:** 网站可能在国内运行，Gemini 在国内通常连不上；国内主流模型（包括 DeepSeek）是文字模型，没有音频输入输出。

**Decision:**
- 在 `VoiceBackend` 接口后面新增 `TextChatBackend`：本地转写（已按说话人分好）→ OpenAI 兼容的文字模型 → 本机语音合成 → 播放。协议新增 `send_transcript`、`interrupt`，原生语音后端忽略它们。
- 用通用的 OpenAI 兼容接口，DeepSeek 只是预设；其他国内模型填地址和模型名即可。
- "该不该说话"靠系统提示里的"[沉默]"约定，不是模型服务端的判断。
- 用户在设置里选择提供商；密钥分别保存。

**Consequences:** 延迟比原生语音高；模型听不到语气；文字模型的表现取决于转写质量；合成声音（macOS `say`）偏机械，以后可以换。
