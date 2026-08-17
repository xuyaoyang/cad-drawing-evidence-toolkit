# v12.5.0

本版本在v12.1.0的中望CAD只读证据工具基础上，增加无需CAD安装的轻量候选读取、
受控AutoCAD辅助后端和版本化自动路由，同时继续保持原始DWG只读、证据不静默合并、
失败安全停止及D4深化自动识别冻结边界。

## 新增与调整

- 新增ACadSharp 3.6.51便携候选后端。上游为
  [DomCR/ACadSharp](https://github.com/DomCR/ACadSharp)，采用MIT许可证；本工具固定
  NuGet版本并只打开校验分析副本。
- 新增单图Agent自动路由：
  `ACadSharp → ZWCAD → AutoCAD 2023 → AutoCAD 2020 → AutoCAD 2018 → AutoCAD 2014`。
- 新增AutoCAD通用现场构建器和Core Console只读运行器。只支持64位R24.2、R23.1、
  R22.0和R19.1，引用目标电脑本机Autodesk程序集，不提交或分发DLL。
- 启动AutoCAD前读取DWG版本头。2023/2020/2018接受到AC1032；2014只接受到AC1027。
  未知或超限格式记录为不兼容，不启动宿主、不覆盖原图、不自动降版。
- 路由契约升级到`cad-backend-route/0.2`，保留每次尝试、触发原因、宿主/API版本、
  DWG版本、输出目录和源文件前后SHA-256。
- 增加只消费既有D4状态与共享梁台账的受限下游汇总器；它不打开DWG、不跨项目识别梁，
  也不恢复被冻结的D4自动深化能力。

## 验证

- 根工具及受限下游：`67 passed, 1 skipped`。
- 仓库内置Codex Skill：`57 passed`。
- Python `compileall`、`MANIFEST.json`和两个路由Schema的JSON语法检查通过。
- 44个PowerShell脚本在PowerShell 7与Windows PowerShell 5.1中解析错误均为0。
- AutoCAD 2023 R24.2已实机完成7个DLL现场编译、合成布局视口图六类JSON导出、
  两套不公开真实DWG字段回归、自动路由和零秒超时清理；分析副本SHA-256不变。
- 发布内容不包含DWG/DXF、工程CSV/JSON、DLL/PDB/EXE、许可证、凭证、客户证据、
  固定个人路径、缓存或临时构建目录。

## 明确边界

- ACadSharp仍是轻量候选后端，不是原生宿主等价替代；ATTRIB、复杂嵌套变换、bulge、
  动态块、MINSERT展开和布局视口等未闭合项必须保留。
- AutoCAD 2020/2018/2014当前只完成源码适配、版本识别、x64/DWG门禁和策略测试；
  在对应真实宿主完成编译、合成图及真实图回归前，不宣称已实机验证或整体等价。
- V18/V16正式数量管线仍以中望CAD为原生执行后端；自动路由不得把不同后端证据静默合并。
- D4阻尼器跨项目梁号、梁高、净空和连接节点自动识别继续冻结，不属于本Release能力。
- 数量与定位结果是设计证据候选，不替代合同数量、专业审图或生产放行。
