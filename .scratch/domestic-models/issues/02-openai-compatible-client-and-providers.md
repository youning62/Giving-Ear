# 02: OpenAI 兼容客户端与提供商设置

**What to build:** `OpenAIChatClient`（流式文字和函数调用，兼容 DeepSeek 等国内接口），提供商注册表（gemini / deepseek / custom），非密钥设置存储（`settings.json`），多个提供商各自的密钥存储；`real_backend` 按设置选择后端；系统提示增加文字模式（带"[沉默]"规则、回复要口语化、不用 markdown）。

**Blocked by:** 01

**Status:** done（2026-11-04）

## 完成记录
- `openai_client.py`：流式文字和函数调用（分片参数拼装）、中文错误提示（401/402/404/429/网络不通）、`ping()` 测试连接。直接用 httpx，没有新增依赖。
- `providers.py`：gemini / deepseek（预设 `https://api.deepseek.com`、`deepseek-chat`）/ custom；非密钥设置存 `settings.json`，各提供商密钥分别存在 env 文件里。
- `app.real_backend` 按设置选择后端；系统提示新增文字模式（带"[沉默]"规则、口语化、不用 markdown）。
- **没验证：** 没有用真实的 DeepSeek 密钥调用过；请求格式是按 OpenAI 兼容接口的通用约定写的，DeepSeek 的具体行为（例如工具调用的细节）需要你用真实密钥确认。


- [x] 测试：流式文本按块返回；函数调用的分片参数被正确拼成完整的 JSON
- [x] 测试：各提供商的密钥互不覆盖，接口不返回完整密钥
- [x] 测试：设置里选 deepseek 得到文字后端；选 gemini 得到 Gemini 后端；custom 必须有地址和模型名，否则给清楚的中文提示
- [x] 测试：没设置所选提供商的密钥时，提示的是这个提供商
- [x] 测试：文字模式的系统提示含"[沉默]"规则；登录/未登录两种提示都有
