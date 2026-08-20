# 新会话启动契约

本文件用于把 CAD 工具包从聊天上下文中解耦。新窗口不得根据上一窗口的口头结论、固定安装路径或临时输出目录直接续跑；先执行 Session Doctor，再从其 JSON 结果选择入口。

## 固定启动步骤

```powershell
.\scripts\检查CAD工具包会话.ps1 `
  -WorkRoot 'D:\CadWork\当前任务\session'
```

从已安装 Codex Skill 启动时使用：

```powershell
& "$env:USERPROFILE\.codex\skills\cad-drawing-evidence-extraction\scripts\检查CAD工具包会话.ps1" `
  -WorkRoot 'D:\CadWork\当前任务\session'
```

Doctor 只读检查工具包版本、必要文件、PowerShell/Python、工作目录安全性、本机 CAD 宿主和已安装 Skill 漂移；它不打开 DWG、不启动 CAD、不编译 DLL。结果固定写入 `cad-toolkit-session.json`。

## 结果门禁

- `overall_status=blocked`：缺少入口、Manifest 无效或工作目录不安全，停止运行。
- `skill_sync.status=drifted`：仓库源码与已安装 Skill 不一致。当前窗口可以显式使用仓库入口继续研发，但不能声称已安装 Skill 在其他窗口等价；发布或交接前必须同步并复跑 Doctor。
- `native_backend_ready=false`：仍可使用 ACadSharp 候选层；出现关键未决后没有可用原生宿主时安全停止，不把候选提升为正式闭合。
- `execution_context=installed_skill`：后续路径必须以 Doctor 返回的 `toolkit_root` 为基准，不再猜测 Git 仓库位置。

## 任务级状态必须落盘

Doctor 只描述工具环境，不替代工程任务记录。每个项目仍须在所属项目的 `HANDOFF.md`、`TASKS.md` 或任务 manifest 中记录：

- 原始输入相对路径和 SHA-256；
- 输入角色、楼栋/楼层/图纸角色；
- 本轮明确入口、参数和工作目录；
- 已完成阶段、输出路径、状态、未决项；
- 禁止消费的验证真值或旧结果。

没有这些落盘状态时，新窗口只能重新发现和预检，不能凭聊天摘要直接把候选升级为确认。
