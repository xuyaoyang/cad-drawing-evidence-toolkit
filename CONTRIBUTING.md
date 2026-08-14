# 版本与GitHub发布约定

本仓库是CAD识图工具的Git版本权威来源。以后每次功能版本更新均按以下流程执行：

1. 从最新`main`创建`agent/<change>`功能分支；
2. 只提交本次功能相关源码、Schema、测试和必要文档；
3. 运行专项测试、全量测试及禁入内容扫描；
4. 推送分支并通过GitHub Pull Request审查后合并到`main`；
5. 正式版本在合并提交上创建带注释的版本标签和GitHub Release；
6. 更新`README.md`、`MANIFEST.json`、`PROJECT_STATUS.md`和`VALIDATION.md`。

严禁提交真实DWG/DXF、项目运行结果、客户/项目证据、编译DLL、许可证、账号、密钥、固定个人路径、
缓存或临时工作目录。项目证据工作区与本仓库保持分离，发布前必须重新扫描暂存区和Git对象。
