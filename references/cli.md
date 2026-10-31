# 命令行参考

PowerShell 包装器与 Python 脚本参数完全相同：

```powershell
& "<skill-dir>\scripts\compress-image.ps1" `
  "C:\path\to\image.png" --max-bytes 900KB
```

## 常用参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--max-bytes` | `900KB` | 单图目标字节上限，支持 `500KB`、`1.5MB`、纯数字 |
| `--max-edge` | `1600` | 最长边像素上限 |
| `--min-quality` | `60` | 自动搜索的最低编码质量 |
| `--format` | `webp` | `auto`、`jpeg`、`png`、`webp`、`avif`、`gif` |
| `--request-limit` | `4MB` | base64 请求体的保守上限；`0` 表示不检查 |
| `--no-resize` | 关闭 | 仅调质量，不改尺寸 |
| `--recursive` | 关闭 | 递归处理目录 |
| `--overwrite` | 关闭 | 覆盖已有输出 |
| `--skip-smaller` | 关闭 | 源图小于目标时跳过重新编码 |
| `--json` | 关闭 | 返回结构化结果 |
| `--dry-run` | 关闭 | 仅分析，不写文件 |

## 输出行为

- 单文件默认写入源文件旁的 `compressed/`。
- 多文件默认各自写入源文件旁的 `compressed/`；传 `--output-dir` 时统一写入该目录。
- 单文件传 `--output` 且路径带扩展名时按文件处理；不带扩展名时按目录处理。
- 输入支持目录、通配符和多个文件。

## 413 排查顺序

1. 把 `--max-bytes` 降到 `700KB`，确认压缩件能被模型接收。
2. 若图片包含长文本或界面细节，保持 `--max-edge 1600` 以上并使用 `--format png` 或 `--format webp`。
3. 若请求体仍超限，检查是否一次附带多张图片；逐张发送并降低每张的体积。
4. Codex 的 413 可能由整个请求体触发，不只看单图大小。多图时按总预算分配，例如 4 张图使用 `--max-bytes 500KB`。

## 质量建议

- 截图与 UI：`--format png --max-edge 2048`，若体积过大再转 WebP。
- 摄影图片：`--format webp --max-edge 1600 --min-quality 65`。
- 需要广泛兼容：`--format jpeg --max-edge 1600`。
- 透明素材：`--format webp`，避免 JPEG 把透明区变黑或变白。
