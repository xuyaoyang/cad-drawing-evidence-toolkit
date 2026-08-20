# CAD 识图工具包 v12.7

> 当前稳定Release：`v12.7.0`（2026-08-20）。本仓库的稳定主线仍以CAD只读证据能力为核心；
> D4阻尼器跨项目自动识别保持冻结。本版本增加新会话稳定性门禁和AutoCAD D2L 2.2
> 梁文字证据后备，不恢复跨项目自动推导梁或净空。

当前版本为`12.7.0`。新窗口先运行`scripts\检查CAD工具包会话.ps1`；它会生成自包含的会话环境清单，检查工具版本、入口完整性、已安装Skill漂移、工作目录安全性和本机后端，避免依赖上一段聊天记忆。详细门禁见[SESSION_BOOTSTRAP.md](SESSION_BOOTSTRAP.md)。

面向结构 DWG 的只读证据提取和阻尼器设计数量核对。单图Agent入口先用ACadSharp生成轻量候选；出现关键未决项时依次尝试中望CAD、64位AutoCAD 2023/2020/2018及受DWG版本限制的2014，原生宿主共用同一套V5/V6/V7/V10/V13导出器源码。本地脚本再完成专业路由、图纸角色判断、重复展示去重、楼栋/楼层展开、型号与 X/Y 方向调和。

本包既可由 Codex Skill 调用，也可由能读取文件并执行本地命令的其他 AI 使用；AI 本身不必具有多模态能力。

## 本次发布范围

| 能力 | 入口/产物 | 当前边界 |
| --- | --- | --- |
| 中望CAD只读提取 | V18/V16统一入口、V20隔离工作器、V5/V6/V7/V10/V13/V18导出器源码 | 目标电脑现场编译；原DWG只读且关闭时不保存 |
| ACadSharp便携候选提取 | `scripts\运行ACadSharp只读候选提取.ps1`、候选证据JSON | 无需安装CAD；只用于字段对照和候选检索，不是中望正式后端等价替代 |
| AutoCAD只读辅助后端 | `autocad\build_autocad_exporters.ps1`、Core Console运行器、`运行AutoCADD2L旁路数据库索引.ps1` | 支持64位2023/2020/2018；2014仅接受AC1027及更早DWG；D2L 2.2可供正式梁高前置证据门禁使用 |
| Agent自动后端路由 | `scripts\运行CAD只读自动后端.ps1`、`cad-backend-route.json` | 固定ACadSharp→中望→AutoCAD 2023→2020→2018→2014；证据不静默合并；`absence_proven=false` |
| 文字索引与检索 | `scripts\生成图纸文字索引.py`、递归文字JSON、坐标/句柄/块路径 | 代理对象或导出跳过项保持未决 |
| 轴网与逐台定位 | V19/V21/V22/V23 | 支持正交、旋转/扇形直轴及满足证据门槛的同心弧轴；不按最近轴号硬配 |
| 阻尼器数量识别 | V16/V18、跨视图物理归一、布局可见性与数量调和 | 输出设计布置数量候选，不替代合同数量或生产放行 |

明确不包含：D4跨项目梁自动识别、从DWG自动推导安装净空、翻墩/墙内自动判断、正式深化DWG、
真实项目图纸、项目运行结果、编译DLL、许可证、账号或密钥。D4冻结状态和恢复条件见
[PROJECT_STATUS.md](PROJECT_STATUS.md)。

## v12.7 AutoCAD D2L 2.2

- `ZwcadSideDatabaseIndexExporterD2L.cs`继续作为一份共享算法源码；构建器只做命名空间映射，
  分别引用目标宿主程序集现场编译，不把中望DLL加载进AutoCAD，也不提交编译产物。
- AutoCAD D2L入口为`autocad\运行AutoCADD2L旁路数据库索引.ps1`。目标DWG与宿主DWG必须是
  两份不同的分析副本；入口在启动前检查64位宿主、API版本和DWG头，运行前后核对两份
  SHA-256，拒绝覆盖已有证据，并只清理本轮新增的Core Console进程。
- 正式成功状态要求`D2L-sidedb-2.2`、无截断、跳过对象为0、每条记录有可见/可打印字段，
  且文字记录有世界方向空间；不满足时输出执行清单并安全停止，不能降级冒充正式梁高证据。
- AutoCAD 2023已用非公开真实梁图与中望按完全相同的展开根参数对照：顶层16960条、直接文字
  10002条、属性270条、块实例55条一致，24910条含递归文字在文字/根句柄/块路径/WCS/方向/
  可见/可打印组合键上逐条一致。两宿主对少量LINE/POLYLINE类型解释仍有8条净差，因此只声明
  正式梁高所需文字证据闭合，不声明所有几何实体整体等价。
- 2020、2018、2014复用同一源码及版本门禁；本机没有对应宿主，当前只确认源码兼容设计和
  静态策略，仍须在装有相应版本的电脑现场编译实跑。2014继续拒绝AC1032，不自动降版。

最短调用（DLL先用同目录构建器现场生成）：

```powershell
.\autocad\运行AutoCADD2L旁路数据库索引.ps1 `
  -TargetDrawingPath 'D:\CadWork\项目\target-copy.dwg' `
  -HostDrawingPath 'D:\CadWork\项目\host-copy.dwg' `
  -AutoCadRoot 'C:\Program Files\Autodesk\AutoCAD 2023' `
  -PluginDir 'D:\CadWork\项目\build' `
  -WorkRoot 'D:\CadWork\项目\autocad-d2l'
```

## v12.5 AutoCAD多版本辅助路由

- 单图只读入口固定依次尝试`ACadSharp → ZWCAD → AutoCAD2023 → AutoCAD2020 → AutoCAD2018 → AutoCAD2014`。ACadSharp如果遇到视口、
  代理/未支持实体、MINSERT、非均匀缩放、缺Handle或遍历问题，候选证据仍保留，
  但路由进入原生宿主。
- 中望安装存在且导出成功时即停止；只有中望未安装、API不可用或执行失败时，
  才按2023、2020、2018、2014检查64位AutoCAD。对应API版本为R24.2、R23.1、
  R22.0、R19.1；其他AutoCAD版本和32位宿主安全停止。
- 路由在启动原生宿主前读取DWG头。2023/2020/2018接受到AC1032；2014只接受到
  AC1027。版本不兼容时记录`incompatible`，不打开CAD，也不自动降版转换。
- AutoCAD DLL不是中望DLL混用。构建脚本对同一份导出器C#源码作命名空间/引用变换，
  再引用本机`AcCoreMgd.dll`/`AcDbMgd.dll`/`AcMgd.dll`现场编译，仓库不提交DLL。
- 已用合成布局视口图和两套非公开真实DWG做只读字段级回归。可比的文字、块实例、
  方向、基础几何、图层/布局/模型展示视口核心字段一致；字体外包框、纸空间整体
  视口相机值和关闭视口运行时编号仅作宿主诊断，不宣称后端整体等价。

单图最短调用：

```powershell
.\scripts\运行CAD只读自动后端.ps1 `
  -InputPath 'D:\项目\sample.dwg' `
  -WorkRoot 'D:\CadWork\sample-route'
```

先读返回的`RouteRecord`；它会保留每次尝试、触发原因、所选后端、输出目录和原图
SHA-256前后值。

## v12.3无CAD安装候选后端

- 新增基于MIT许可ACadSharp 3.6.51的Windows只读候选读取器；首次构建下载固定NuGet包并
  校验SHA-256，依赖、EXE和DLL只写入仓库外的本机缓存。
- 运行器先计算原图SHA-256、复制到非同步工作区、核对副本，再只打开副本；运行结束再次
  核对原图哈希；单次读取默认300秒超时，只终止本轮候选读取器。
- 输出TEXT、MTEXT、ATTRIB、ATTDEF模板、INSERT、LINE、LWPOLYLINE、POINT、CIRCLE、ARC候选，
  保留Handle、根空间、块路径和递归坐标，并完整汇总解析通知、未支持实体及遍历问题。
- 当前状态固定属于候选比较层。布局视口可见性、嵌套有效图层、动态块、xref、代理对象、
  MINSERT及非均匀缩放几何仍未闭合；`formal_backend_equivalent=false`、
  `absence_proven=false`。
- 已用两套真实DWG、原生中望变换合成图和既有布局视口合成图完成字段回归。可比的根插入、
  普通文字、MTEXT、直线和圆心覆盖较好，但复杂嵌套坐标、ATTRIB、带bulge多段线外包框、
  非均匀圆弧、MINSERT展开、动态块和视口可见性仍未闭合。部分ATTRIB坐标与中望不一致，
  因此属性坐标明确标记为
  `parser_value_not_backend_equivalent`，不能送入正式数量/定位流程。详见
  [portable/README.md](portable/README.md)和[CAD_BACKEND_COMPATIBILITY.md](CAD_BACKEND_COMPATIBILITY.md)。

### ACadSharp来源与授权

- 上游源码：[DomCR/ACadSharp](https://github.com/DomCR/ACadSharp)，由DomCR维护；NuGet页面列出的
  包所有者为`DomCr`，版权人为Albert Domenech。
- 本工具固定使用官方[NuGet ACadSharp 3.6.51](https://www.nuget.org/packages/ACadSharp/3.6.51)，
  许可证为[MIT](https://github.com/DomCR/ACadSharp/blob/master/LICENSE)。固定NuGet包SHA-256为
  `E66741A44848C6D1F9CF935DA72716F6A84924EA5D5EC494F5644C41AA98D97B`。
- ACadSharp上游是可读写DWG/DXF的通用C#库；本仓库没有复制其源码或提交其DLL，只通过固定NuGet
  包构建自己的候选读取器，并且本工具入口只实现“打开校验后的分析副本并读取”，不暴露DWG写入路径。
- 本项目不是ACadSharp官方项目，也不代表上游作者对本工具的工程结果、字段等价性或适用性背书。

## v12.2受限下游汇总器

- 新增`experimental\deepening`，将已经存在的逐台D4状态和共享梁台账整理为CSV/JSON。
- 输出上下梁顶标高、上梁高度、上下支承降板状态/高度、未计上翻墩的梁间净空，以及上下梁
  几何生根状态。
- 一台设备一行，设备ID缺失/重复或预期数量不一致时安全停止；缺少证据不填零。
- 文字梁标注或双梁面辅助链不能单独确认生根，必须有已确认物理梁段。
- 该功能不读取DWG、不恢复D4自动识别、不判断墙内、上翻墩、承载力、配筋或生产放行。

## 最短使用路径

1. 将整个目录复制到目标 Windows 电脑。
2. 安装能正常打开目标 DWG 的中望 CAD，并确认安装目录内存在
   `fonts\HZTXT.SHX` 和 `fonts\simplex.shx`。首次许可和启动设置须人工完成。
   不同电脑安装位置不同，可先运行
   `.\zwcad\发现CAD安装.ps1 -Vendor Any`；V18/V16在未传`-ZwcadRoot`时也会
   自动发现已安装且API程序集完整的中望CAD。
3. 运行前关闭用户正在操作的中望 CAD；无人值守入口检测到已有 ZWCAD 进程会
   默认安全停止，不会复用或终止用户会话。
4. 原图保持只读；副本、编译结果和中间证据写入本机非同步工作目录。未传 `-WorkRoot` 时默认使用 `%LOCALAPPDATA%\CadReadingToolkit\Work`，也可设置环境变量 `CAD_READING_WORK_ROOT`。
5. 先对项目目录做文件名分层和实际内容复筛：

```powershell
.\scripts\运行CAD阻尼器数量核对V18.ps1 `
  -InputPath 'D:\项目\设计输入' `
  -WorkRoot 'D:\CadWork\项目-V18' `
  -ContentScanOnly
```

6. 确认复筛结果后，对目录运行完整流程：

```powershell
.\scripts\运行CAD阻尼器数量核对V18.ps1 `
  -InputPath 'D:\项目\设计输入' `
  -WorkRoot 'D:\CadWork\项目-V18'
```

先看 `<WorkRoot>\output\V18目录内容复筛.md`，完整流程再看
`<WorkRoot>\full\output\V16运行汇总.md` 与
`<WorkRoot>\v19\V19跨DWG证据组.md`。单个用户明确指定的 DWG 仍可直接运行
V16。原 DWG 只复制、校验 SHA-256、只读打开，流程关闭图纸时不保存。

## v12.1环境与AI路由

- v12.1新增`zwcad\发现CAD安装.ps1`：v12.5的发现结果已能区分可运行中望、
  可运行64位AutoCAD 2023/2020/2018/2014和超出验证范围的其他AutoCAD。详见
  [CAD_BACKEND_COMPATIBILITY.md](CAD_BACKEND_COMPATIBILITY.md)。
- v12.1当时AutoCAD仅完成发现；v12.4新增AutoCAD 2023实机后端，v12.5扩展
  2020/2018及受DWG版本门禁的2014。2020/2018/2014仍须在对应真实宿主上完成实机回归。
- 基础AI没有图像能力时，可复制`MULTIMODAL_ASSISTANT.example.json`并配置独立
  多模态服务；密钥只通过环境变量提供。配置检查入口为
  `scripts\validate_multimodal_assistant_config.py`。
- 多模态只用于V24等高风险局部证据的辅助复核，不能改变V19/V21/V23正式状态。
  本仓库不发布自动梁尺寸或安装净空识别；下游若在完全无多模态条件下识别梁尺寸，
  已知正确率较低，必须只输出候选/资料不足并人工复核。

## V12 新增能力

- V23生成后自动调用V24，把证据链未决、OUT、弧形轴网、上游未闭合和高偏差列为P0/P1并全部复核。
- 旋转/扇形直轴、较大跨视图比例偏差和靠近边界列为P2；普通正交轴网列为P3，P2/P3按楼栋确定性分层抽样。
- 输出`V24AI轻量抽查清单.json`，AI只读取抽中任务及其SVG，避免把全部SVG/JSON/CSV同时放入上下文。
- 输出`V24中望回查定位.lsp`；`V24OPEN`只读打开非OneDrive工作副本并定位，`V24GOTO`核对当前图名后缩放和临时高亮句柄。
- 原图、同步目录文件、缺失工作副本或当前图名不一致时安全停止；V24不改变V19/V21/V23状态。

## V11 已有能力

- V19在生成V21逐台定位后自动调用V23，重新计算每个已定位物理模板的轴间位置。
- 输出逐台证据CSV/JSON、每模板一张局部SVG和可离线打开的HTML抽查索引。
- 正交轴保留轴号文字句柄；旋转/扇形直轴同时保留最近平行轴线实体句柄。
- 弧轴保留圆弧、切向延伸、轴号文字句柄和轴号—圆弧匹配距离。
- 原定位与重算轴间/比例不一致，或任一必要句柄缺失时，保持
  `evidence_trace_unresolved`，不改变V19数量或可见性状态。
- V23也可直接接收V12清单和对应的`physical_device.csv`，用于既有结果补做
  抽查包。

## V10 能力

- V10.1 基础几何增加圆弧世界圆心、半径、起点、中点和终点。
- V22 用同一轴号两端文字建立旋转/扇形直轴，用唯一轴号—圆弧关系、共同圆心
  和切向直线延伸建立弧轴家族。
- 设备只有唯一落入直轴条带/扇区或弧轴有效角域/切线范围时才定位；不按最近
  轴号强配。
- 最外轴外侧不超过相邻轴距25%的近邻记录为 `最后轴>OUT@比例`，不虚构下一轴。
- V21 的正交文字轴网回退、V20 无人值守中望和既有数量安全门槛全部保留。

## V9 新增能力

- V19 对只有主视图的证据组也运行V12，不再以“无跨视图来源”为由跳过定位。
- 输出 `V21阻尼器定位总表.csv/json/md` 和定位未决表；逐台保留楼栋、楼层、
  数字/字母轴间及相对比例、主实例键和主视图世界坐标。
- 单栋明确楼层可安全展开；多栋共用平面必须有逐栋楼层调和证据，禁止自动乘算。
- 主实例未落入轴间、无证据地同时落入多套轴网、定位字段缺失或设备ID重复时
  自动安全停止。
- V8 的无人值守字体替代、逐图隔离、失败续跑和配置恢复全部保留。

## V8 能力

- V20 为缺失普通 SHX 和中文大字体生成任务本地 FMP，临时映射到中望自带
  `simplex.shx` 与 `HZTXT.SHX`，并关闭文件/命令对话框，避免无人值守被字体
  提示卡住。
- 每张 DWG 运行在独立子进程；单图超时或失败只清理本轮新建 ZWCAD，记录后
  继续下一张，结束时恢复用户原 FONTALT、FONTMAP、FILEDIA 和 CMDDIA。
- 修改字体配置前写入 `%LOCALAPPDATA%\CadReadingToolkit\v20-font-recovery.json`；
  即使整批父进程被外部强杀，下一次V20也会先恢复旧配置再处理新图。
- V19 支持 `02a-2-1/02a-2-A` 一类“命名空间+轴号”，但仅在证据组唯一楼栋
  时归一；多楼栋共用图继续安全停止。
- `3-7#` 一类复合楼栋号保持为一个标识，避免拆成3#与7#。
- 19项标准库测试和真实中望入口回归通过。

## 既有核心能力

- V13：导出实体/父实例/有效图层、动态属性及布局视口可见性。
- V14：按唯一实例键判断模型对象进入哪些视口，并去除跨视口重复展示。
- V15：可复现的布局视口冻结层合成 DWG 与真值回归。
- V16：统一执行专业路由、六类中望 API 导出、图框归属、阻尼器计数、布局视口分析和状态汇总。
- V17：按专业、图纸角色、日期文件族和相同 SHA-256 对目录分层。
- V18：在完整六导出前用实际 CAD 内容复筛辅助/不确定图，降低文件名漏选。
- V19/V21/V22/V23：将原主图与升级图按楼栋/楼层自动组成跨DWG证据组，调用V12并用文字或直线/圆弧几何生成逐台定位总表，再生成句柄支撑的局部SVG抽查包。
- V20：自动字体映射、逐图隔离超时、失败续跑和用户配置恢复。
- 目录输入时只自动处理明确的结构专业 DWG；建筑图仅在结构证据存在疑点时另行复核，其他专业默认不进入数量主流程。

## 结论边界

- 自动结果是“设计布置数量候选”，不是合同供货数量或生产放行数量。
- V18 的内容命中只表示该文件应升级处理，不等于识别出设备数量。
- `content_negative` 固定不证明图中不存在阻尼器；`content_unresolved` 必须保留待核实。
- `frame_evidence_required`、`manual_review_required`、`layout_viewport_unresolved`、`visibility_export_unresolved` 都是安全停止。
- 不能用数量表、抽检数量或说明文字补齐图面实例。
- 代理对象、图框归属、一墙多机和跨专业矛盾无法闭合时，必须保留为待人工核实。
- 替代字体只保证 API 流程不中断；字形、字宽、排版和特殊符号可能变化，正式
  视觉出图必须补齐原字体复核。
- 自由曲线、样条、椭圆、断裂轴或缺轴号端点的轴网仍须人工核查。

## Codex 安装

将 `codex-skill\cad-drawing-evidence-extraction` 复制到
`%USERPROFILE%\.codex\skills\`，刷新 Codex 后使用。本仓库不分发隔震支座或
阻尼器深化设计专用规则。

其他 AI 直接先读 [AI_WORKFLOW.md](AI_WORKFLOW.md)、[ENVIRONMENT.md](ENVIRONMENT.md) 和 [OUTPUT_CONTRACT.md](OUTPUT_CONTRACT.md)，不依赖 Codex 的 Skill 发现机制。

## 包内容

- `scripts\`：V18/V16 统一入口、V19跨DWG编排、V23几何定位证据包、目录内容复筛及数量、表格、跨视图、布局视口分析脚本。
- `portable\`：ACadSharp只读候选读取器源码、固定依赖校验和仓库外构建脚本；不含二进制。
- `autocad\`：64位AutoCAD 2023/2020/2018及受限2014的版本策略、共享源码构建适配器和Core Console只读运行器。
- `zwcad\`：六类完整导出器与V18轻量指纹导出器的 C# 源码、外部构建脚本、V20隔离批量/单图工作器和 V15 合成回归。
- `codex-skill\`：通用CAD证据提取的 Codex 适配层。
- `experimental\deepening\`：仅消费既有状态的最小净空/几何生根汇总器；不含识图输入和项目结果。
- 不含 DWG/DXF、编译 DLL、许可证、账号、令牌、缓存或项目运行结果。
