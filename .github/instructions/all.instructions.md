---
applyTo: **
---

每次修改后请:

1. 更新USER_GUIDE.md
2. 更新版本号
3. 修改完成后使用 `bash build-macos.sh < /dev/null 2>&1 | tee /tmp/build-output.log` 构建 macOS 安装包，并检查构建日志以确保没有错误。确认无误后删除日志。
