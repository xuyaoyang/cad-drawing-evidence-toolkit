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
