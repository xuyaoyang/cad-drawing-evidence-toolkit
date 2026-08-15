# AI 通用工作流

## 目标

使用ACadSharp候选层与中望/AutoCAD 2023只读API自动建立结构DWG的可回查证据，并核对阻尼器设计布置数量。最终结果应覆盖：专业路由、图纸角色、楼栋、楼层/连续楼层、型号、X/Y 方向、跨图框/跨视图/跨布局重复展示、数量表调和和未闭合项。

## 执行顺序

1. 读取本文件、`ENVIRONMENT.md`、`CAD_BACKEND_COMPATIBILITY.md`和
   `OUTPUT_CONTRACT.md`；先运行`zwcad\发现CAD安装.ps1 -Vendor Any`核对本机宿主。
   单图证据检索用`scripts\运行CAD只读自动后端.ps1`，固定ACadSharp先执行；
   出现关键未决项后先看中望，中望不存在/不可用/失败才看AutoCAD 2023 R24.2。
   V18/V16正式数量管线仍不静默换宿主。
2. 对项目目录运行 `scripts\运行CAD阻尼器数量核对V18.ps1 -ContentScanOnly`；单个用户明确指定的 DWG 可直接进入 V16。
3. 审查 V17 分层和 V18 内容复筛：`selected` 为原主图；`promoted_primary` 为实际可达内容命中并升级的辅助/不确定图；`keep_supporting`、`reference_hit`、`content_negative` 和 `content_unresolved` 不自动计数。
4. 只把 `selected + promoted_primary` 送入完整六导出。旧日期、相同哈希副本和内容未决项不得静默提升；内容阴性不等于图中没有目标。
5. 关闭用户正在操作的中望CAD；确认安装目录内有 `HZTXT.SHX` 与
   `simplex.shx`。运行不带 `-ContentScanOnly` 的 V18 入口，由V20逐图隔离、
   自动处理缺字体提示并在结束后恢复配置。不得让 AI 用鼠标逐个数图替代 API 主流程。
6. 先看 `V18目录内容复筛.md`，再看 `full\output\V16运行汇总.md` 和对应分析目录中的候选 CSV、调和 CSV、V14 报告及原始 JSON。
   如需按图框快速检索设计说明和关键词，可对V5图框归属CSV运行：

   ```powershell
   python .\scripts\生成图纸文字索引.py --frames <图框候选.csv> --texts <文字归属.csv> --output <图纸文字索引.md>
   ```

   索引只汇总API原文、句柄和坐标，不从相邻文字补全缺失内容。
7. 查看 `v19\V19跨DWG证据组.md` 和 `V21阻尼器定位总表.md`。V19按楼栋、楼层、主视图和梁/柱/墙/板辅助视图自动分组，在唯一主视图及轴网充分时调用V12；只有主视图也必须生成V21定位。V22可联合轴号文字与V10.1直线/圆弧几何处理旋转、扇形和同心弧轴网。升级文件的单图数量仍不得直接相加。
8. 先读 `v19\V23几何定位证据\V24风险分层抽查\V24AI轻量抽查清单.json`，
   再只打开抽中任务对应的SVG。P0/P1全部复核，P2/P3按楼栋分层抽样；不得把
   V23全部SVG、JSON和CSV同时塞入模型上下文。需要回原图坐标核查时，在中望
   加载`V24中望回查定位.lsp`，只对非OneDrive分析副本使用`V24OPEN/V24GOTO`。
   V23/V24只解释已定位模板，不能把缺主图或未定位证据组改成闭合。
   基础模型不支持图像时，可按`MULTIMODAL_ASSISTANT.example.json`配置独立
   多模态助手；配置结果必须经`validate_multimodal_assistant_config.py`验证，
   多模态输出仍只能辅助复核，不能单独形成正式确认。
9. 查看 `output\V20中望导出执行.csv` 和 `output\font-policy\font-policy.json`；
   任何超时、字体文件缺失、配置恢复异常或命令未完成都必须保留为安全停止。
10. 仅在证据状态允许时报告设计数量候选；否则说明卡在哪一层、缺什么证据。

本仓库不提供梁高或安装净空自动确认。外部深化模块若没有任何多模态通道，梁尺寸
判读的已验证可靠性更低，应保留为候选/资料不足并要求人工复核，不能用最近文字补齐。

## 证据优先级

1. V5：递归文字、属性、世界坐标、句柄和块路径。
2. 图框 V5：闭合多段线、线段和块范围，用于图纸角色及文字归属。
3. V6：唯一根/嵌套实例键、块定义、父实例路径、世界范围。
4. V7：逐文字世界方向，用于 X/Y 方向证据。
5. V10.1：直线、圆弧、闭合多段线和世界几何；既可用带文字实例作为种子识别无文字同构符号，也可用圆心、半径和采样点建立V22弧轴。
6. V13/V14：有效图层、动态属性、布局视口、冻结层、关闭视口和重复展示。
7. 建筑图或 CAD 视觉：只用于结构证据无法唯一归属、轴网方向不清或代理对象不可读的疑点。

V18 内容指纹位于以上完整证据之前，只负责决定文件是否升级，不是新的数量证据层。

## 数量规则

- 根块插入次数不等于设备数量；应识别语义叶子或逐位置符号。
- 同一物理设备在相邻图纸、梁/墙/结构平面、深化图或多个布局视口重复展示时只计一次，但保留全部表达记录。
- 多栋共用同一平面、多个楼层共用同一布置时，只有楼栋/楼层适用范围和每层模板均有证据才能展开。
- 参数表数量只可用于调和，不能代替图面逐实例核对。
- WCS X/Y 只有在建筑轴与世界轴一致或已取得轴网基准时才可直接采用。
- 旋转/扇形直轴必须由同轴号两个远端位置确定并唯一落入相邻扇区；弧轴必须
  轴号—圆弧唯一、共圆心且具有切向直线延伸。最外轴近邻最多外推相邻轴距25%，
  记为 `OUT` 而不是新增轴号。
- 大样显示一墙多机、文字与几何冲突、图框角色不明、视口裁剪/冻结无法解析时必须安全停止。

## 可报告状态

- `design_quantity_candidate_reconciled`：图面实例、适用范围、型号/方向或数量表证据内部一致，可报告设计数量候选。
- `layout_instance_candidate_ready`：布局可见性闭合，但尚无充分楼栋/楼层/图框证据，只报告唯一可见实例候选。
- `frame_evidence_required`：缺可靠图框/图纸角色证据，不得补算。
- `manual_review_required`：存在未归属或冲突记录，需要定位核查。
- `layout_viewport_unresolved`：视口、裁剪、冻结或重复展示证据未闭合。
- `visibility_export_unresolved`：V13 导出错误、缺记录或可见性证据不足。
- `export_incomplete`：六类必需导出不完整。
- `not_selected`：专业路由未选择该文件。

`not_selected` 也可能对应 `supporting`、`reference`、`older_revision`、`exact_duplicate` 或 `uncertain`；它不等于证明图中没有阻尼器。

V18还使用：

- `promoted_primary`：实际内容证据足以升级；
- `keep_supporting`：保留跨视图候选；
- `reference_hit`：只作参数/范围参考；
- `content_negative`：当前API可读范围未命中，`absence_proven=false`；
- `content_unresolved`：代理对象、跳过对象或导出失败使复筛未闭合。

V19还使用：

- `primary_layout_missing`：同范围没有可计数主视图；
- `multiple_structural_primary_views` / `multiple_layout_primary_views`：主视图不唯一；
- `primary_axis_evidence_missing`：主视图轴网不足；
- `single_primary_device_location_complete`：只有主视图，但逐台楼栋、楼层、轴间和主视图坐标定位完整；
- `single_primary_device_location_unresolved`：主视图设备未可靠落入轴间，或无证据地同时落入多套轴网；
- `device_location_floor_scope_unresolved`：楼层不能安全分配到楼栋；
- `device_location_registry_complete/partial/unavailable`：V21总表完整、部分可用或不可用；
- `cross_view_identity_unresolved`：至少一个辅助视图实例无法映射；
- `cross_view_identity_consistent_visibility_unverified`：身份一致，可见性未闭合；
- `cross_view_quantity_closed`：当前API范围内身份与数据库可见性闭合。

V23还使用：

- `v23_located_template_evidence_complete`：所有已定位模板均已重算一致，且所需
  轴号/直线/圆弧/切向延伸句柄完整；不表示所有V19证据组已定位。
- `v23_evidence_package_partial`：至少一个已定位模板的证据链缺失或重算不一致。
- `evidence_trace_complete/unresolved`：单模板证据链完整或未决。

V24还使用：

- `P0/P1`：证据未决或复杂高风险项，全部进入抽查；
- `P2/P3`：中低风险项，按楼栋确定性分层抽样；
- `v24_sampling_ready`：抽查清单和中望工作副本回查任务均可用；
- `v24_sampling_ready_project_partial`：抽查包可用，但仍有未定位证据组或
  缺安全工作副本，不能据此提升项目状态。

## 合成回归

V15 合成图只用于验证布局视口冻结层算法，不是工程数量证据。期望真值为 10 个唯一语义实例、2 个跨视口重复、6 次冻结层隐藏、1 个关闭视口。
