---
name: image-compress
description: 在把图片发给模型前按体积压缩图片，处理 HTTP 413 Payload Too Large、超大截图、照片、透明 PNG 和批量图片。适用于需要控制单图字节数、估算 base64 请求体、缩小长边、转换 WebP/JPEG 或批量整理图片的场景。
---

# 图片压缩

当图片附件导致模型请求返回 `413 Payload Too Large`，或需要把图片控制在指定体积以内时，使用本 Skill 的脚本。

## 工作流

1. 使用 Python 3.10+（推荐 3.12）运行脚本，路径相对于本 Skill 目录：
   `scripts/image_compress.py`（相对于本 Skill 目录）
   也可以直接调用 PowerShell 启动器：
   `scripts/compress-image.ps1`
2. 默认目标为单图不超过 900 KB、最长边 1600 px、输出 WebP。普通截图或照片通常足够清晰。
3. 输入单文件时先直接压缩；输入目录时加 `--recursive`，需要覆盖原图时才加 `--output` 指向源文件。
4. 优先把压缩结果作为新的图片附件发给模型，保留原图。
5. 若仍触发 413，降低 `--max-bytes` 与 `--max-edge` 后重试，例如 `--max-bytes 500KB --max-edge 1280`。

## 命令

```powershell
py -3.12 "<skill-dir>\scripts\image_compress.py" `
  "C:\path\to\image.png" `
  --max-bytes 900KB `
  --max-edge 1600
```

批量目录：

```powershell
py -3.12 "<skill-dir>\scripts\image_compress.py" `
  "C:\path\to\images" `
  --recursive `
  --output-dir "C:\path\to\compressed" `
  --max-bytes 900KB `
  --max-edge 1600
```

需要透明背景时输出 WebP；需要兼容老旧上传接口时使用 `--format jpeg`。动图只在 `--format webp` 或 `--format gif` 下保留动画。

## 结果判断

- 脚本保证最终文件字节数不超过 `--max-bytes`。
- `请求体约` 是 multipart + base64 的保守估算，默认检查 4 MB；`--request-limit 0` 可关闭这项检查。
- 默认不覆盖原图；同名输出自动加序号。
- 使用 `--json` 可获得适合程序解析的路径、尺寸、质量和请求体估算。

详细参数和边界见 [references/cli.md](references/cli.md)。
