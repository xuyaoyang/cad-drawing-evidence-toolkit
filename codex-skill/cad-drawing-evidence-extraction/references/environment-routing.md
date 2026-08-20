# 导出通道与适用边界

## 中望 CAD .NET：块内文字优先

- 使用 `scripts/zwcad/ZwcadTextExporterV2.cs` 的 `CADTEXTEXPORT5` 命令递归导出标准文字和块定义文字到 JSON。
- 使用 `ZwcadSymbolExporterV6.cs`、`ZwcadOrientedTextExporterV7.cs` 和 `ZwcadPrimitiveGeometryExporterV10.cs` 分别导出唯一块实例、文字世界方向及基础几何。
- 需按本机中望版本重新编译；不得假设其他电脑上的 DLL 可以加载。
- 编译时显式传入中望安装目录，并把 DLL 写入 OneDrive 之外的临时构建目录。
- 代理对象、天正/PKPM 专业对象仍可能不可读，需保留为待人工核实并转视觉或专业插件路径。
- 项目目录的常规数量核对优先运行V18统一入口。它先用V16 `-RouteOnly`取得V17目录分层：阻尼器专项/结构平面/结构整图为 `selected`；梁柱墙板、基础和节点为 `supporting`；目录/总说明为 `reference`；用户指定目录顶层的“项目名+八位日期”DWG为项目包候选；同文件族较早日期为 `older_revision`；同 SHA-256 副本为 `exact_duplicate`；其他专业为 `excluded`；证据不足为 `uncertain`。
- V18只预扫描 `supporting/uncertain` 副本，并只递归实际插入可达块定义。形成 `promoted_primary` 的文件与原 `selected` 一起进入完整六导出；`keep_supporting/reference_hit/content_negative/content_unresolved` 均不自动计数。文件名日期不是正式版本证明，内容阴性也不证明图中不存在目标。
- 单个用户明确指定的 DWG 可直接运行 V16，不必先做目录内容复筛。
- V16必须把原图复制到显式 `-WorkRoot`、`CAD_READING_WORK_ROOT` 或默认 `%LOCALAPPDATA%\CadReadingToolkit\Work`，核对哈希后从副本只读导出。工作目录不得位于OneDrive/同步目录；不得让导出器把JSON写入原图相邻目录。
- 无人值守统一使用V20隔离批处理：目标电脑必须有已注册的中望COM/.NET环境和安装目录内的`HZTXT.SHX`、`simplex.shx`。V20用任务本地FMP临时替代常见缺失SHX/中文大字体，并把FILEDIA/CMDDIA设为0；逐图超时后只清理本轮新进程并继续，最终恢复原配置。变更前在`%LOCALAPPDATA%\CadReadingToolkit`写入恢复账本，整批进程被外部强杀时由下一次V20启动先恢复。已有用户ZWCAD进程时默认拒绝启动，避免复用或误关用户会话。
- 字体替代的目的仅是避免弹窗阻塞API证据导出；字形、字宽、排版和特殊符号仍可能变化。需要视觉核对或正式出图时必须补齐原字体，不能把替代字体渲染当作图纸原貌。

## AutoCAD Core Console：中望后的多版本原生辅助后端

- 只支持64位AutoCAD 2023 R24.2、2020 R23.1、2018 R22.0、2014 R19.1；
  其他版本和32位宿主安全停止。
- `scripts/autocad/build_autocad_exporters.ps1`转换共享导出器源码的引用/命名空间，
  再引用本机Autodesk程序集编译；不得加载中望DLL。
- 启动宿主前读取DWG头：2023/2020/2018最多接受AC1032，2014最多接受AC1027；
  未知或超限版本记录`incompatible`，禁止自动降版转换。
- Core Console只打开经SHA-256校验的分析副本，关闭时丢弃更改，并要求
  V5/V6/V7/V10/V13六类JSON齐全。
- 只有ACadSharp遇到关键未决且中望未安装/不可用/失败时才进入该后端。
- 宿主外包框、纸空间整体视口相机值和运行时视口编号仅作诊断；
  `backend_equivalent=false`、`absence_proven=false`不可提升。
- 当前实机字段回归仅覆盖AutoCAD 2023；2020/2018/2014须在对应真实宿主验证后
  才能提升其宿主验证状态。
- 正式梁文字所需D2L 2.2可使用
  `scripts/autocad/运行AutoCADD2L旁路数据库索引.ps1`。它要求目标与宿主为两份
  不同分析副本，现场构建DLL，核对两份DWG运行前后SHA-256，并拒绝跳过、截断、
  缺可见/可打印或文字方向字段的输出。AutoCAD 2023真实梁图文字证据已与中望逐条
  闭合；LINE/POLYLINE仍存在少量宿主类型解释差异，不能据此宣称所有几何等价。

## 视觉核对：只处理 API 不足的事实

仅在图框范围、布置符号逐个数量、节点/预埋件对应、代理对象、复杂旋转/合并表格或实体与图面矛盾时打开 CAD。视觉结果也必须记录图号/区域与人工核查范围，不能替代原始实体证据。
