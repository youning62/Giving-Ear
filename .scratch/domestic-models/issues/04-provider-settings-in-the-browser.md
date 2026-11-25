# 04: 网页里选择模型

**What to build:** 设置里增加"模型"选择（Gemini / DeepSeek / 自定义），每个提供商各自的密钥输入，自定义时填地址和模型名，语音选择；"测试连接"按钮；开始监听前检查当前所选提供商的密钥。

**Blocked by:** 02

**Status:** done（2026-11-04）

## 完成记录
- 接口：`GET /api/state` 返回提供商列表（是否有密钥、末 4 位）和设置；`POST /api/settings/provider`；`POST /api/settings/key` 带提供商；`DELETE /api/settings/key?provider=`；`POST /api/settings/test`（测试连接）。
- 网页设置弹窗：选模型、自定义接口地址和模型名、各自的密钥、中文声音、测试连接；首次引导提示会写明当前选的是哪个模型。已用无头 Chrome 截图确认布局。
- **没验证：** 弹窗里按钮的点击流程没有自动化点过。


- [x] 测试：`GET /api/state` 返回提供商列表（是否有密钥、末 4 位）和当前设置，不含完整密钥
- [x] 测试：`POST /api/settings/provider` 保存选择；非法提供商、自定义缺地址返回清楚的 400
- [x] 测试：`POST /api/settings/key` 带提供商，只改这个提供商的密钥
- [x] 测试：`POST /api/settings/test` 用假客户端验证成功/失败时的中文提示
- [x] 网页：设置弹窗按提供商切换表单，国内网络提示
