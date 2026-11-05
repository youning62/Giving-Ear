# 04: 命令行：账号与声纹管理

**What to build:** `givingear account create|list|passwd|delete`，`givingear speakers list|rename|delete`（都对当前账号），运行时 `givingear --account 名字`（密码用不回显的提示输入，也可用环境变量 `GIVINGEAR_PASSWORD` 供脚本使用）。取代原票 09。

**Blocked by:** 01, 03

**Status:** done（2026-11-04）

## 完成记录
- `src/givingear/account_cli.py`：`givingear account create|list|passwd|delete`，`givingear speakers [--account 名字] list|rename|delete`，运行时 `--account`（密码错误直接退出，不碰麦克风）。
- 密码用不回显的提示输入；脚本里可用环境变量 `GIVINGEAR_PASSWORD`（改密码时新密码用 `GIVINGEAR_NEW_PASSWORD`）。只有一个账号时 `speakers` 可以省略 `--account`。
- 取代原票 09（已作废）。


- [x] 测试：创建账号、列出账号、改密码、删除账号（删除需要输入密码确认）
- [x] 测试：`speakers list` 显示名字和登记时间；没有档案时提示"暂无已登记的说话人"
- [x] 测试：`speakers delete 名字` 后，用同一账号运行，这个人被当作陌生人；删除不存在的名字给中文提示、不报错退出
- [x] 测试：`--account` 密码错误时给清楚的中文提示并退出，不启动麦克风
