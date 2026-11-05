# 09: 档案管理命令

**What to build:** 用户可以在命令行管理已注册的声纹档案：`givingear speakers list` 列出所有已注册档案（名字、注册时间），`givingear speakers delete <名字>` 删除指定档案。删除后，这个人的声纹特征从本地档案库彻底移除，下次运行时就不再认出他，他会被当作陌生人。名字不存在时给出清楚的中文提示。参考规格：`.scratch/givingear-v1/spec.md`。

**Blocked by:** 08（口头注册与永久保存档案）

**Status:** superseded by .scratch/accounts（票 04）

- [ ] `givingear speakers list` 显示所有已注册档案；没有档案时提示"暂无已注册的说话人"
- [ ] `givingear speakers delete <名字>` 删除档案，并确认删除成功
- [ ] 测试：删除后用同一个档案库运行核心，这个人的语料被标为"说话人 N"
- [ ] 删除不存在的名字时，给出中文提示，不报错退出
