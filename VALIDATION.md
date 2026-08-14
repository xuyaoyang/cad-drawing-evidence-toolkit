# 发布验证

## 2026-08-14 v12.3 ACadSharp候选后端复核

- 第二阶段增加原生中望合成变换图，独立覆盖嵌套、旋转、非均匀缩放、MINSERT、ATTDEF、
  ATTRIB、直线、多段线、圆和圆弧；合成DWG及真值只保存在仓库外临时区。同一候选证据双跑
  字节一致。
- 合成变换图在1e-6口径下：TEXT+ATTDEF兼容口径8/8、块身份5/5、世界插入点5/5、
  根INSERT变换4/4、LINE 5/5、LWPOLYLINE 4/4、圆心4/4；ATTRIB为3/4，嵌套INSERT
  的全部实例变换诊断为4/5。MINSERT正确读取2行、3列、行距400、列距300，但不展开实例。
- 既有布局视口合成图：TEXT 16/16、INSERT身份及变换11/11、LINE 38/38；中望V13记录
  2个布局和5个视口，便携后端仍明确标记视口可见性未实现。
- 两套非公开真实DWG只在仓库外只读副本上回归，原图哈希前后不变；真实输入、哈希、逐字段
  统计和输出不进入本仓库。回归确认ATTRIB、部分复杂嵌套变换、深层LINE和带bulge多段线
  不能按当前字段直接等价，动态块有效属性也未实现。状态继续保持未解决。

- 使用ACadSharp 3.6.51固定NuGet包，包SHA-256为
  `E66741A44848C6D1F9CF935DA72716F6A84924EA5D5EC494F5644C41AA98D97B`；源码在仓库，
  包、DLL和EXE只在`G:\CodexWork`临时区构建。
- 非公开真实DWG只读副本运行和重复性检查成功；原图运行前后哈希不变，真实输入、哈希、统计和
  运行输出均未进入仓库。真实字段对比发现部分ATTRIB坐标不一致，且存在解析通知和未支持实体，
  故正式等价门禁保持关闭，状态为`portable_readonly_candidate_unresolved`。
- PowerShell 7与Windows PowerShell 5.1均完成候选运行；1秒故障注入正确记录
  `timed_out=true`和退出码124，终止后候选读取器残留进程为0，原图SHA不变。
- 全仓测试使用隔离导入模式：`88 passed, 1 skipped`；候选输出结构校验通过。
- 32个PowerShell脚本在PowerShell 7.6.4和Windows PowerShell 5.1解析错误均为0；新增中望
  合成插件针对本机中望机械CAD 2026 API现场编译并成功生成DWG和真值JSON。

## 2026-08-14 v12.2受限下游汇总器复核

- 新增的最小净空与上下梁几何生根导出器仅消费既有D4状态和共享梁台账；不打开DWG，
  不包含跨项目梁自动识别、真实项目状态或运行结果。
- 根目录与新增汇总器测试：`45 passed, 1 skipped`；Codex Skill内置测试：`37 passed`。
- Python `compileall`通过；`MANIFEST.json`语法校验通过。
- 暂存候选文件中未发现DWG/DXF、项目CSV/JSON、图片、Office/PDF、DLL或固定个人路径。
- 真实项目回归结果只保留在隔离的工程证据工作区，未进入本仓库；工程验证结论不得由
  本仓库的合成/单元测试替代。

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
