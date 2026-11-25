# 03: 网页里选引擎并试听

**What to build:** 设置页的"AI 的声音"：选引擎，各引擎的参数，DashScope 密钥；"试听"按钮：选一个情绪，用当前设置合成一句示例并在浏览器里播放。

**Blocked by:** 02

**Status:** done（2026-11-05）

## 完成记录
- `POST /api/settings/test-voice`：用当前设置合成一句，返回 base64 的 WAV；失败返回 ok:false 和中文原因。`GET /api/state` 带引擎列表、情绪词表、DashScope 密钥状态。
- 设置页"AI 的声音"：选引擎、各引擎的参数、DashScope 密钥、选情绪后"试听"（在浏览器里播放）。已截图确认布局。
- **没验证：** 试听按钮的点击和浏览器播放没有自动化点过。


- [x] 测试：`POST /api/settings/test-voice` 返回可播放的 WAV（base64）；失败返回 ok:false 和中文提示；不泄露密钥
- [x] 测试：`GET /api/state` 带情绪词表和 DashScope 密钥状态（不含完整密钥）
- [x] 网页：引擎选择、参数、试听
