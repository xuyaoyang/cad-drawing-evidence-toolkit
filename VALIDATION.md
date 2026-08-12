# 发布验证

## 2026-08-12 v12.1发布复核

- 新增中望/AutoCAD安装发现；本机中望机械CAD 2026标记为当前可运行后端，
  AutoCAD 2023保持`discovery_only_backend_not_validated`。
- V18/V16省略`-ZwcadRoot`时可以自动发现中望；`-RouteOnly`仍不要求或启动CAD。
- 新增可选多模态路由配置、Schema和3项门禁测试；默认关闭，密钥不写入配置或输出，
  `formal_confirmation_allowed`固定为`false`。
- AutoCAD后端、D4梁识别和安装净空仍不在发布能力内。
- 根目录测试`40 passed`，Skill内置测试`37 passed`；Python `compileall`通过。
- 28个PowerShell脚本在PowerShell 7与Windows PowerShell 5.1解析错误均为0。
- Codex Skill校验通过；本机中望机械CAD 2026 API重新编译7个导出器全部成功，
  DLL仅保存在`G:\CodexWork`临时验证目录。
- V16省略`-ZwcadRoot`的`-RouteOnly`冒烟通过，生成预检报告且未启动CAD进程。
- 禁入扩展名、固定个人路径和凭证扫描结果均为0。

## 2026-08-07私有仓库发布前复核

- Python测试：`37 passed`，包含参数化文字索引入口的独立目录回归。
- Python源码：`compileall`通过；生成的缓存受`.gitignore`排除。
- PowerShell：26个脚本在PowerShell 7和Windows PowerShell 5.1解析均无错误。
- Codex Skill：`quick_validate.py`通过。
- 中望CAD现场编译：使用中望机械CAD 2026（26.0.128.3）的本机API程序集，
  V5文字、V5图框、V6实例、V7方向、V10几何、V13可见性及V18内容指纹共
  7个导出器全部编译成功；DLL只保存在本机非同步临时验证目录，未进入仓库。
- 发布内容门禁：不含DWG、DXF、PDF、图片、项目表格、DLL、PDB、PYC、许可证、
  凭证、真实项目结果或个人绝对路径。

## V12既有真实回归基线

V12在原开发工作区封装时还完成过以下真实/合成回归；本次发布复用同一份V12
源码，但未把真实图纸和运行结果带入仓库：

- 四川工程职大：164个模板分层抽取17项，覆盖9栋，V24状态
  `v24_sampling_ready`。
- 成都生物城：8个复杂模板全部进入强制复核，6个缺主图组保持单列，V24状态
  `v24_sampling_ready_project_partial`。
- 中望机械CAD 2026：在非同步只读分析副本上加载V24回查LISP并完成坐标/句柄
  定位，退出后无残留进程。

这些结果证明工具链可以运行，不表示所有设计院DWG都能自动闭合。正式使用仍须
遵守`OUTPUT_CONTRACT.md`中的候选、未决和安全停止状态。
