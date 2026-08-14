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

V18/V16入口可以省略`-ZwcadRoot`。需要启动CAD时，脚本会选择已发现且API程序集
完整的中望CAD；`-RouteOnly`仍不启动或要求CAD。

## 当前已验证宿主

- 当前源码、无人值守进程和字体恢复逻辑均以中望CAD为执行后端。
- 实机调试环境为中望机械CAD 2026；其他中望版本必须在目标电脑现场重新编译并做只读冒烟。
- 实际安装目录和版本必须进入运行记录，不能沿用另一台电脑的路径。

## AutoCAD兼容要求

AutoCAD与中望CAD的对象模型相近，但程序集、命名空间、COM ProgID、Core Console、
字体配置和进程名并不相同。当前工具可以发现AutoCAD安装和
`AcMgd.dll`/`AcDbMgd.dll`，但尚未提供经过实机回归的AutoCAD导出后端。因此：

1. 禁止把`ZwManaged.dll`编译的插件加载进AutoCAD；
2. AutoCAD后端必须单独引用本机`AcMgd.dll`与`AcDbMgd.dll`，并实现与现有
   V5/V6/V7/V10/V13/V18相同的JSON证据契约；
3. 逐项回归TEXT、MTEXT、ATTRIB、嵌套块变换、Handle/WCS、Polyline/Hatch、
   Layout/Viewport、Xref、代理对象、只读关闭和超时清理；
4. 通过合成样本和至少两套真实DWG的字段级对比之前，状态必须是
   `discovery_only_backend_not_validated`，不得宣称AutoCAD兼容；
5. 未安装中望、只有AutoCAD时，当前统一入口应安全停止，不能静默切换。

当前可执行并已验证的仍是中望CAD后端；AutoCAD只是完成安装发现和适配边界定义。

## 无CAD安装时的ACadSharp候选后端

`scripts\运行ACadSharp只读候选提取.ps1`可以在没有中望或AutoCAD的Windows电脑上运行。
它使用固定版本ACadSharp 3.6.51，只打开经SHA-256核对的工作副本，并输出独立的
`acadsharp-portable-evidence/0.1`候选契约。

该通道不是V16的自动降级后端，当前只能用于文字/几何检索候选和字段级比较：

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

未安装中望时，Agent可以显式调用该候选入口，但必须保留
`portable_readonly_candidate_unresolved`等原状态，不能把阴性结果写成“图中没有”。
