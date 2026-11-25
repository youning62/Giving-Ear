# 02: 情感语音引擎（通义千问云端、本地 CosyVoice）

**What to build:** `QwenCloudTTS`（DashScope 的 `qwen3-tts-instruct-flash`）和 `CosyVoiceTTS`（调用用户自己部署的 CosyVoice FastAPI 服务）；设置项和密钥（`DASHSCOPE_API_KEY`）；`real_backend` 按设置选择引擎。

**Blocked by:** 01

**Status:** done（2026-11-05），**只用假服务器测过，没有用真实密钥/服务验证**

## 完成记录
- `tts_expressive.py`：`QwenCloudTTS`（`qwen3-tts-instruct-flash`，`instructions` 字段、取回音频地址、解码 WAV）和 `CosyVoiceTTS`（`instruct2` 上传参考录音、`instruct` 预置音色，原始 PCM 按设置的采样率解释）。错误都有中文提示。
- `voices.py` 按设置选引擎；`providers.Settings` 新增引擎和各引擎参数，保存时校验；DashScope 密钥单独存。
- **没验证：** 通义千问和 CosyVoice 的真实返回、情绪指令用中文写的效果（参考资料里的例子是英文）、CosyVoice 各版本对指令格式的要求。


- [x] 测试：Qwen 请求带对模型、音色、情绪指令；解析返回的音频地址并取回 WAV；错误（密钥、额度、网络）有中文提示
- [x] 测试：CosyVoice 两种模式（instruct / instruct2）的请求格式；返回的原始 PCM 按设置的采样率解释；服务没启动时提示清楚
- [x] 测试：没有 DashScope 密钥时提示的是它；设置校验（引擎名、地址、采样率）
- [x] 测试：选情感引擎时后端的提示里有情绪标签说明
