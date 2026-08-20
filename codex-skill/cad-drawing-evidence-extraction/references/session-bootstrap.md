# 新会话启动

新窗口、换电脑或不确定Skill版本时，先运行：

```powershell
& "$env:USERPROFILE\.codex\skills\cad-drawing-evidence-extraction\scripts\检查CAD工具包会话.ps1" `
  -WorkRoot 'D:\CadWork\当前任务\session'
```

Session Doctor不打开DWG、不启动CAD、不编译DLL。它把工具版本、实际工具根目录、必要文件、PowerShell/Python、工作目录安全性、本机CAD宿主和Skill同步状态写入`cad-toolkit-session.json`。

- `overall_status=blocked`时停止。
- `skill_sync.status=drifted`时，不得声称已安装Skill与仓库或其他窗口等价。
- `native_backend_ready=false`时只允许ACadSharp候选路径；关键未决无法由原生宿主闭合时安全停止。
- 后续所有脚本路径以JSON中的`toolkit_root`为基准，不猜Git仓库或上一窗口的临时目录。

Doctor只描述工具环境。工程输入SHA、角色、运行参数、已完成阶段、输出和未决项仍须落入项目`HANDOFF.md`、`TASKS.md`或任务manifest；没有落盘状态时，新窗口不得凭聊天摘要把候选提升为确认。
