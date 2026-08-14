# 最小净空与上下梁几何生根导出器

本目录提供一个受限的下游汇总器：消费已经存在的逐台D4当前状态和共享梁台账，输出一台一行的
CSV、可追溯JSON和边界说明。它不会打开DWG，也不会执行梁识别、跨楼层配准或项目特定推断。

## 输入

- `D4-unified-formal-support-beam-integrated-clearance-state-1.0`或兼容状态；
- `D4-unified-formal-support-beam-ledger-1.0`或兼容共享梁台账；
- 可选的预期设备数，用于遗漏/重复安全停止。

## 输出字段

- 设备ID、楼栋、楼层、分区、轴号、方向和可用型号；
- 下梁顶标高、上梁顶标高、上梁高度；
- 上下支承降板状态和高度；
- 梁间净空，固定标注为“未计上翻墩”；
- 上下梁几何生根结论、证据状态和未决原因。

## 运行

```powershell
python .\experimental\deepening\scripts\export_d4_minimal_clearance_anchorage.py `
  --state "D:\work\d4-current-state.json" `
  --beam-ledger "D:\work\d4-formal-beam-ledger.json" `
  --output-dir "D:\work\逐台净空与生根" `
  --expected-device-count 100
```

## 安全边界

- 只有设备投影唯一绑定已确认物理梁段时，才自动确认几何生根。
- 文字梁号、截面或双梁面辅助链缺少已确认物理梁段时保持候选。
- 几何生根不表示承载力、配筋或节点设计验算通过。
- 不判断墙内隐蔽，不计算上翻墩尺寸，不生成正式深化图，不作生产放行。
- 仓库不附带任何真实项目状态、共享梁台账、CSV结果或DWG。

当前目录属于受限下游工具，不表示已恢复冻结的D4跨项目自动识别研发。
