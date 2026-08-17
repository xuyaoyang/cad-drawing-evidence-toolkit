# 发布验证

## 2026-08-17 v12.5.0正式Release复核

- 发布范围为`v12.1.0..v12.5.0`的通用源码、Schema、测试和文档，包含受限下游
  状态汇总器、ACadSharp 3.6.51便携候选后端、AutoCAD 2023实机辅助后端以及
  AutoCAD 2020/2018/2014版本化门禁；D4跨项目深化自动识别仍冻结且不进入Release。
- 发布标签只能在Release文档PR合并后的`main`提交上创建；GitHub Release不附加本机
  构建资产，只使用GitHub自动生成的源码归档。
- 发布前重新执行根工具及受限下游测试、Skill测试、Python `compileall`、JSON语法检查、
  PowerShell 7/5.1解析、根/Skill同步哈希、禁入扩展名、固定个人路径及凭证扫描。
- 根工具及受限下游`67 passed, 1 skipped`，Skill`57 passed`；44个PowerShell脚本
  在PowerShell 7和Windows PowerShell 5.1中解析错误均为0，10组根/Skill核心文件
  SHA-256一致。
- `v12.1.0`以来及本次Release文档共66个新增/修改文件：禁入扩展名、固定个人路径和
  凭证明文命中均为0；Python `compileall`和3个JSON文件语法检查通过。
- 2020/2018/2014仍为源码适配和门禁验证，未安装对应宿主，不得因正式发布而改写为
  实机等价；AutoCAD 2014继续拒绝AC1032且不自动转换。

## 2026-08-17 v12.5 AutoCAD 2020/2018/2014版本化适配

- AutoCAD版本策略固定为64位2023 R24.2、2020 R23.1、2018 R22.0、2014 R19.1；
  自动路由顺序为`ACadSharp → ZWCAD → AutoCAD2023 → AutoCAD2020 → AutoCAD2018 → AutoCAD2014`。
- DWG头门禁在启动原生宿主前执行。2023/2020/2018上限为AC1032，2014上限为
  AC1027；未知或超限格式只记录`incompatible`，不启动宿主、不自动降版转换。
- 当前AutoCAD 2023 R24.2实机用通用构建器重新编译7个DLL成功；V15合成视口图
  完整导出六类JSON成功，分析副本SHA-256不变。故障注入中望路径后，单图路由正确
  选中`autocad_2023_native_fallback_selected`，`cad-backend-route/0.2`语义校验通过。
- AutoCAD Core Console零秒超时故障注入后，本轮新增`accoreconsole`进程为0，分析副本
  SHA-256不变；已有输出仍拒绝覆盖或复用。
- 根工具及受限下游测试`67 passed, 1 skipped`，Skill内置测试`57 passed`；Python
  `compileall`通过，44个PowerShell脚本在PowerShell 7和Windows PowerShell 5.1
  解析错误均为0。两个路由Schema和`MANIFEST.json`均通过JSON语法检查。
- 2020/2018/2014当前电脑未安装，本轮只完成源码、版本识别、x64门禁、DWG兼容门禁
  和合成策略测试；在对应真实宿主完成7 DLL编译、合成图与真实图字段回归前，不表述为
  已实机验证或后端等价。

## 2026-08-15 v12.4 AutoCAD 2023辅助后端与自动路由

- 路由顺序固定为`ACadSharp → ZWCAD → AutoCAD2023`。合成视口DWG实跑分别命中
  `zwcad_native_fallback_selected`和（故障注入中望路径后）
  `autocad_2023_native_fallback_selected`；两份路由记录通过语义校验。
- AutoCAD 2023 R24.2现场编译7个DLL成功；Core Console对两套非公开真实DWG及
  合成视口DWG只读导出成功，分析副本SHA-256前后不变。真实DWG和逐图输出
  不进入本仓库。
- 非公开大图字段对照中，TEXT 14202/14202、图框线5285/5285、块实例
  1154/1154、方向文字14066/14066、基础几何40534/40534、可见性实例
  1154/1154、图层374/374、布局2/2的可比核心字段闭合。
- 合成视口图中4个模型展示视口核心字段4/4闭合。纸空间整体视口不再
  进入模型设备映射；关闭视口的运行时编号差异只作诊断。
- 边界仍为`backend_equivalent=false`、`absence_proven=false`；字体/实体外包框差异不作核心
  等价证明，非AutoCAD 2023 R24.2版本明确拒绝。
- AutoCAD Core Console正常导出和零秒超时故障注入均保持分析副本哈希不变；
  超时后本轮新增`accoreconsole`PID为0，已有输出文件时拒绝覆盖或复用。
- 根工具测试`56 passed`，Skill内置测试`51 passed`，受限下游测试`5 passed, 1 skipped`；
  Python compileall通过，40个PowerShell脚本在PowerShell 7和Windows PowerShell 5.1解析错误均为0。

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
  包、DLL和EXE只在仓库外的本机临时区构建。
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
  DLL仅保存在仓库外的临时验证目录。
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
