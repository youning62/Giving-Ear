# 03: 语音合成

**What to build:** `Synthesizer` 接口和 `MacSayTTS`（macOS 自带 `say`，离线）。中文和英文自动选声音；临时文件用完即删。

**Blocked by:** 无

**Status:** done（2026-11-04）

## 完成记录
- `tts.py`：`MacSayTTS`（macOS `say` → 24 kHz PCM，临时文件用完即删；中文用 Tingting、其他用 Samantha）。声音偏机械，接口可替换，以后可换本地神经网络合成或云端合成。


- [x] 测试（仅 macOS）：合成一句中文，得到非空、时长合理的 24 kHz 单声道 PCM；中英文分别选对声音
- [x] 测试：合成过程中没有在临时目录之外留下任何文件；空文本返回空
