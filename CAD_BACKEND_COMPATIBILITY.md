# CAD宿主发现与兼容边界

## 先发现安装位置

不同电脑不得复用示例路径。先执行：

```powershell
.\zwcad\发现CAD安装.ps1 -Vendor Any | Format-Table
```

发现顺序为：`CAD_ZWCAD_ROOT`/`CAD_AUTOCAD_ROOT`环境变量、Windows卸载注册表、
常见磁盘的`Program Files`目录。自动发现仍可能遗漏绿色版或定制部署；遗漏时显式传
`-ZwcadRoot`，或设置`CAD_ZWCAD_ROOT`。入口必须检查EXE和托管API DLL真实存在，
不能只凭目录名。

V18/V16入口仍是中望专用的正式数量管线。单DWG的Agent轻量/辅助后端路由使用
`scripts\运行CAD只读自动后端.ps1`。

## 当前已验证宿主

- V18/V16正式数量管线及字体恢复逻辑仍以中望CAD为执行后端。
- 实机调试环境为中望机械CAD 2026；其他中望版本必须在目标电脑现场重新编译并做只读冒烟。
- 单图辅助后端已实机验证AutoCAD 2023 R24.2 Core Console；不包括更高版本。
- 实际安装目录和版本必须进入运行记录，不能沿用另一台电脑的路径。

## AutoCAD 2023辅助后端

AutoCAD与中望CAD的对象模型相近，但程序集、命名空间、COM ProgID、Core Console、
字体配置和进程名并不相同。v12.4的适配范围为：

1. 禁止把`ZwManaged.dll`编译的插件加载进AutoCAD；
2. `autocad\build_autocad_2023_exporters.ps1`对共享的中望导出器源码作编译时
   命名空间转换，单独引用本机`AcCoreMgd.dll`/`AcDbMgd.dll`/`AcMgd.dll`；
3. `autocad\AutoCADCoreConsole只读导出.ps1`仅接受R24.2，只打开已校验的分析副本，
   终止时丢弃更改，并核对前后SHA-256和六类必需JSON；
4. 合成布局视口图与两套非公开真实DWG已做字段级回归。文字、块实例、方向、
   基础几何、图层、布局和模型展示视口的可比核心字段闭合；
5. 字体/实体外包框、纸空间整体视口相机值、关闭视口运行时编号只作诊断。
   `backend_equivalent=false`和`absence_proven=false`仍不可提升；
6. 未安装中望、只有AutoCAD 2023时，自动路由可以切到AutoCAD辅助后端；
   非R24.2版本继续安全停止。

## Agent自动路由

`scripts\运行CAD只读自动后端.ps1`严格使用以下顺序：

1. ACadSharp先读；
2. 有关键未决项时检查并运行中望CAD；
3. 中望不存在、不可用或失败时，检查并运行AutoCAD 2023 R24.2；
4. 三者均不能闭合时输出`manual_review_required_no_backend`。

每个后端的证据都保留在独立目录，不静默合并或覆盖。路由记录必须符合
`schemas/cad-backend-route.schema.json`，并可用`scripts/validate_cad_backend_route.py`校验。

## 无CAD安装时的ACadSharp候选后端

`scripts\运行ACadSharp只读候选提取.ps1`可以在没有中望或AutoCAD的Windows电脑上运行。
它使用固定版本ACadSharp 3.6.51，只打开经SHA-256核对的工作副本，并输出独立的
`acadsharp-portable-evidence/0.1`候选契约。

该通道仍不是V16正式数量管线的替代，但是v12.4单图Agent路由的第一步：

1. `formal_backend_equivalent=false`和`absence_proven=false`不可提升；
2. 布局视口裁剪、冻结层、最终可见性和嵌套块有效图层未实现；
3. 动态块有效状态、xref完整性、代理对象内部内容、MINSERT展开和非均匀缩放圆弧仍未验证；
4. 2026-08-14已完成两套非公开真实DWG、原生中望嵌套/旋转/非均匀缩放/MINSERT合成图、既有
   布局视口合成图的字段级回归；真实图及其结果不进入本仓库。该回归用于暴露差异，不是替代认证；
5. 真实回归确认普通TEXT、MTEXT、根INSERT变换和圆心存在可对应字段，但ATTRIB、部分嵌套
   INSERT世界位置/组合变换、深层嵌套LINE、带bulge多段线、动态块和视口仍未形成等价字段；
6. `scripts\compare_acadsharp_portable_with_zwcad.py`只比较两端语义可对齐的字段。ATTDEF与
   ZWCAD V5 DBText继承口径、圆的旋转外包框、嵌套局部/组合变换均单列边界，不以提高匹配率
   为由改写坐标。正式替代仍需新的契约、盲测门槛和下游适配审查。

无原生宿主或原生宿主失败时，Agent必须保留
`portable_readonly_candidate_unresolved`等原状态，不能把阴性结果写成“图中没有”。
