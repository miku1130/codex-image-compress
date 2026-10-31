#!/usr/bin/env python3
"""按目标体积压缩图片，适配 Codex 图片请求的 413 限制。"""

from __future__ import annotations

import argparse
import base64
import json
import math
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
except ImportError as exc:  # pragma: no cover - runtime dependency guard
    raise SystemExit(
        "需要 Pillow 12。请使用 py -3.12 运行，或执行: py -3.12 -m pip install pillow"
    ) from exc


IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".jfif",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".tif",
    ".tiff",
    ".avif",
    ".heic",
    ".heif",
}

DEFAULT_MAX_BYTES = 900 * 1024
DEFAULT_REQUEST_LIMIT = 4 * 1024 * 1024
DEFAULT_TARGET_MAX_EDGE = 1600
DEFAULT_MIN_QUALITY = 60


class CompressionError(RuntimeError):
    """可向命令行使用者直接展示的压缩错误。"""


@dataclass
class Result:
    source: Path
    output: Path
    source_bytes: int
    output_bytes: int
    width: int
    height: int
    format: str
    quality: int | None
    request_bytes: int
    skipped: bool = False


def human_bytes(value: int) -> str:
    units = ("B", "KB", "MB", "GB")
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"


def parse_size(value: str) -> int:
    text = value.strip().upper().replace(" ", "")
    if not text:
        raise argparse.ArgumentTypeError("体积不能为空")
    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
    }
    for suffix in ("GB", "MB", "KB", "G", "M", "K", "B"):
        if text.endswith(suffix):
            number = text[: -len(suffix)]
            try:
                result = float(number) * multipliers[suffix]
            except ValueError as exc:
                raise argparse.ArgumentTypeError(f"无效体积: {value}") from exc
            if not math.isfinite(result) or result <= 0:
                raise argparse.ArgumentTypeError(f"体积必须大于 0: {value}")
            return int(result)
    try:
        result = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"无效体积: {value}") from exc
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError(f"体积必须大于 0: {value}")
    return int(result)


def parse_edge(value: str) -> int:
    try:
        edge = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"无效边长: {value}") from exc
    if edge < 16:
        raise argparse.ArgumentTypeError("边长至少为 16 像素")
    return edge


def expand_inputs(paths: Iterable[Path], recursive: bool) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        path = raw.expanduser()
        candidates: list[Path]
        if any(ch in str(path) for ch in "*?["):
            parent = path.parent if str(path.parent) else Path.cwd()
            candidates = sorted(parent.glob(path.name))
        elif path.is_dir():
            pattern = "**/*" if recursive else "*"
            candidates = sorted(path.glob(pattern))
        else:
            candidates = [path]
        for candidate in candidates:
            if not candidate.is_file():
                continue
            if candidate.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            resolved = candidate.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(candidate)
    return found


def normalized_frame(frame: Image.Image) -> Image.Image:
    """统一成可稳定编码的 RGB/RGBA 图，并修正手机照片方向。"""
    if frame.mode in ("RGB", "RGBA"):
        return frame
    if frame.mode in ("P", "PA"):
        frame = frame.convert("RGBA" if "transparency" in frame.info else "RGB")
    elif frame.mode in ("L", "LA", "1"):
        frame = frame.convert("RGBA" if frame.mode in ("LA", "1") else "RGB")
    else:
        frame = frame.convert("RGBA" if "A" in frame.getbands() else "RGB")
    return frame


def find_encoder(frame: Image.Image, fmt: str) -> tuple[str, dict]:
    """Pillow 的 encoder 名称随版本变化，这里做一次兼容选择。"""
    fmt = fmt.lower()
    if fmt == "jpeg":
        if frame.mode != "RGB":
            frame = frame.convert("RGB")
        return "JPEG", {
            "quality": 90,
            "optimize": True,
            "progressive": True,
            "subsampling": "4:2:0",
        }
    if fmt == "webp":
        return "WEBP", {"quality": 90, "method": 6}
    if fmt == "avif":
        return "AVIF", {"quality": 80, "speed": 6}
    if fmt == "png":
        return "PNG", {"optimize": True, "compress_level": 9}
    if fmt == "gif":
        return "GIF", {"optimize": True}
    raise CompressionError(f"不支持的输出格式: {fmt}")


def resize_frame(frame: Image.Image, max_edge: int) -> Image.Image:
    width, height = frame.size
    longest = max(width, height)
    if longest <= max_edge:
        return frame
    scale = max_edge / longest
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return frame.resize(size, Image.Resampling.LANCZOS)


def scaled_dimensions(size: tuple[int, int], max_edge: int) -> tuple[int, int]:
    width, height = size
    if max(width, height) <= max_edge:
        return size
    scale = max_edge / max(width, height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def encode_frame(
    frame: Image.Image,
    fmt: str,
    quality: int | None = None,
    metadata: bool = False,
) -> bytes:
    if fmt == "jpeg":
        frame = frame.convert("RGB")
    encoder, options = find_encoder(frame, fmt)
    if encoder == "JPEG":
        options["quality"] = max(1, min(95, quality or 82))
    elif encoder == "WEBP":
        options["quality"] = max(1, min(100, quality or 82))
    elif encoder == "AVIF":
        options["quality"] = max(1, min(100, quality or 70))
    if encoder in {"PNG", "GIF"}:
        options.pop("quality", None)
    if not metadata and encoder in {"JPEG", "WEBP", "PNG"}:
        options["exif"] = b""
    output = _encode_bytes(frame, encoder, options)
    return output


def _encode_bytes(frame: Image.Image, encoder: str, options: dict) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    try:
        frame.save(buffer, format=encoder, **options)
        return buffer.getvalue()
    except (OSError, ValueError, TypeError):
        if encoder == "AVIF":
            raise CompressionError("当前 Pillow 不支持 AVIF 编码，请改用 --format webp")
        raise


def encode_animation(
    image: Image.Image,
    fmt: str,
    quality: int | None,
    max_edge: int,
) -> bytes:
    from io import BytesIO

    # ImageSequence 只能在仍打开的源图对象上迭代；复制帧到新列表后再写文件。
    frames: list[Image.Image] = []
    for frame in ImageSequence.Iterator(image):
        current = normalized_frame(frame)
        current = resize_frame(current, max_edge)
        # WebP 编码后仍复用的是第一帧的编码器，后续帧必须保留独立图像对象。
        current = current.copy()
        current.info = {}
        frames.append(current)
    # GIF 不支持完整 alpha，WebP 则保留 RGBA 以避免透明边缘变黑。
    if fmt == "gif":
        frames = [frame.convert("RGB") for frame in frames]
    buffer = BytesIO()
    duration = max(20, int(image.info.get("duration") or 100))
    save_options = {
        "save_all": True,
        "append_images": frames[1:],
        "loop": image.info.get("loop", 0),
    }
    if fmt == "gif":
        save_options.update({"duration": duration, "optimize": True, "disposal": 2})
        frames[0].save(buffer, format="GIF", **save_options)
    elif fmt == "webp":
        save_options.update(
            {
                "duration": duration,
                "quality": max(1, min(100, quality or 75)),
                "method": 6,
            }
        )
        frames[0].save(buffer, format="WEBP", **save_options)
    else:
        raise CompressionError("动画仅支持输出 webp 或 gif")
    return buffer.getvalue()


def make_variants(
    source: Path,
    fmt: str,
    target_bytes: int,
    max_edge: int,
    min_quality: int,
    no_resize: bool,
) -> tuple[bytes, int, int, int | None]:
    with Image.open(source) as opened:
        animated = bool(getattr(opened, "n_frames", 1) > 1)
        if animated:
            if fmt not in {"gif", "webp"}:
                raise CompressionError("动图只能输出为 gif 或 webp")
            return _search_animation(opened, fmt, target_bytes, max_edge, min_quality, no_resize)
        opened = ImageOps.exif_transpose(opened)
        frame = normalized_frame(opened)
        return _search_still(frame, fmt, target_bytes, max_edge, min_quality, no_resize)


def _search_still(
    frame: Image.Image,
    fmt: str,
    target_bytes: int,
    max_edge: int,
    min_quality: int,
    no_resize: bool,
) -> tuple[bytes, int, int, int | None]:
    # PNG 是无损格式，若原图已过小则只需重排压缩；其余格式按质量与尺寸联合搜索。
    edge = min(max_edge, max(frame.size)) if not no_resize else max(frame.size)
    if fmt == "png":
        candidates = [resize_frame(frame, edge)]
        if not no_resize:
            current = candidates[0]
            while max(current.size) > 32:
                current = resize_frame(current, max(32, int(max(current.size) * 0.82)))
                candidates.append(current)
        for candidate in candidates:
            data = encode_frame(candidate, "png", None)
            if len(data) <= target_bytes:
                return data, candidate.width, candidate.height, None
        raise CompressionError(
            "PNG 在当前尺寸下仍超过目标体积，请改用 --format webp/jpeg 或降低 --max-edge"
        )

    quality_floor = max(1, min_quality)
    for current_edge in _edge_steps(edge, no_resize):
        resized = frame if current_edge == max(frame.size) else resize_frame(frame, current_edge)
        best: tuple[bytes, int] | None = None
        low, high = quality_floor, 95 if fmt != "avif" else 90
        while low <= high:
            quality = (low + high) // 2
            data = encode_frame(resized, fmt, quality)
            if len(data) <= target_bytes:
                best = (data, quality)
                low = quality + 1
            else:
                high = quality - 1
        if best:
            data, quality = best
            # 最终再做一次字节数校验，防止编码器在极小画布上产生异常大文件。
            if len(data) <= target_bytes:
                return data, resized.width, resized.height, quality
    raise CompressionError(
        "在当前质量下限与尺寸范围内仍超过目标体积，请降低 --min-quality 或 --max-edge"
    )


def _edge_steps(start: int, no_resize: bool) -> list[int]:
    if no_resize:
        return [start]
    steps: list[int] = []
    current = start
    while current >= 64:
        steps.append(current)
        current = int(current * 0.82)
    steps.append(max(16, current))
    return list(dict.fromkeys(steps))


def _search_animation(
    image: Image.Image,
    fmt: str,
    target_bytes: int,
    max_edge: int,
    min_quality: int,
    no_resize: bool,
) -> tuple[bytes, int, int, int | None]:
    start_edge = min(max_edge, max(image.size)) if not no_resize else max(image.size)
    for edge in _edge_steps(start_edge, no_resize):
        width, height = scaled_dimensions(image.size, edge) if not no_resize else image.size
        if fmt == "gif":
            data = encode_animation(image, fmt, None, edge)
            if len(data) <= target_bytes:
                return data, width, height, None
            continue
        low, high = max(1, min_quality), 95
        best: tuple[bytes, int] | None = None
        while low <= high:
            quality = (low + high) // 2
            data = encode_animation(image, fmt, quality, edge)
            if len(data) <= target_bytes:
                best = (data, quality)
                low = quality + 1
            else:
                high = quality - 1
        if best:
            return best[0], width, height, best[1]
    raise CompressionError("动图压缩后仍超过目标体积，请减小 --max-edge")


def request_bytes_for(data: bytes) -> int:
    """估算 multipart + base64 后常见的图片上传体积，留出边界安全余量。"""
    encoded = base64.b64encode(data)
    # image field、multipart 边界与常见 API 包装字段留约 64 KB 余量。
    return len(encoded) + 64 * 1024


def output_path_for(source: Path, output: Path | None, output_dir: Path | None, fmt: str) -> Path:
    suffix = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "avif": ".avif", "gif": ".gif"}[fmt]
    if output is not None:
        if len(str(output)) > 1 and output.is_dir():
            return output / f"{source.stem}-compressed{suffix}"
        if output.suffix:
            return output
        return output / f"{source.stem}-compressed{suffix}"
    folder = output_dir if output_dir is not None else source.parent / "compressed"
    return folder / f"{source.stem}-compressed{suffix}"


def unique_path(path: Path, overwrite: bool) -> Path:
    if overwrite or not path.exists():
        return path
    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def compress_one(
    source: Path,
    output: Path | None,
    output_dir: Path | None,
    fmt: str,
    target_bytes: int,
    request_limit: int | None,
    max_edge: int,
    min_quality: int,
    no_resize: bool,
    overwrite: bool,
    skip_smaller: bool,
    dry_run: bool,
) -> Result:
    source_bytes = source.stat().st_size
    if skip_smaller and source_bytes <= target_bytes:
        destination = output_path_for(source, output, output_dir, fmt)
        if not dry_run and destination.resolve() != source.resolve():
            destination = unique_path(destination, overwrite)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        width, height = image_dimensions(source)
        return Result(
            source,
            destination,
            source_bytes,
            source_bytes,
            width,
            height,
            source.suffix.lstrip(".").upper(),
            None,
            request_bytes_for(source.read_bytes()),
            skipped=True,
        )
    data, width, height, quality = make_variants(
        source, fmt, target_bytes, max_edge, min_quality, no_resize
    )
    if len(data) > target_bytes:
        raise CompressionError(
            f"压缩结果 {human_bytes(len(data))} 超过目标 {human_bytes(target_bytes)}"
        )
    request_bytes = request_bytes_for(data)
    if request_limit is not None and request_bytes > request_limit:
        raise CompressionError(
            f"base64 请求体约 {human_bytes(request_bytes)}，超过 --request-limit {human_bytes(request_limit)}"
        )
    destination = output_path_for(source, output, output_dir, fmt)
    destination = unique_path(destination, overwrite)
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return Result(
        source=source,
        output=destination,
        source_bytes=source_bytes,
        output_bytes=len(data),
        width=width,
        height=height,
        format=fmt.upper(),
        quality=quality,
        request_bytes=request_bytes,
    )


def image_dimensions(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return image.size
    except (OSError, ValueError):
        return 0, 0


def infer_format(source: Path, requested: str, keep_format: bool) -> str:
    if requested != "auto":
        return requested
    suffix = source.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".jfif"}:
        return "jpeg"
    if suffix == ".png":
        return "png"
    if suffix == ".webp":
        return "webp"
    if suffix == ".gif":
        return "gif"
    if suffix in {".avif", ".heic", ".heif"}:
        return "webp" if not keep_format else "avif"
    return "webp"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="将图片压到指定体积，适配 Codex 图片上传的 413 限制。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("inputs", nargs="+", type=Path, help="图片、目录或通配符路径")
    parser.add_argument("-o", "--output", type=Path, help="输出文件或目录；默认放在源目录 compressed/")
    parser.add_argument("--output-dir", type=Path, help="批量输出目录")
    parser.add_argument("--max-bytes", type=parse_size, default=DEFAULT_MAX_BYTES, help="单个图片目标字节数")
    parser.add_argument("--request-limit", type=parse_size, default=DEFAULT_REQUEST_LIMIT, help="base64 请求体估算上限；0 表示不检查")
    parser.add_argument("--max-edge", type=parse_edge, default=DEFAULT_TARGET_MAX_EDGE, help="最长边像素上限")
    parser.add_argument("--min-quality", type=int, default=DEFAULT_MIN_QUALITY, help="最低编码质量")
    parser.add_argument("--format", choices=("auto", "jpeg", "png", "webp", "avif", "gif"), default="webp", help="输出格式；auto 跟随输入")
    parser.add_argument("--keep-format", action="store_true", help="尽量保持输入格式")
    parser.add_argument("--no-resize", action="store_true", help="只调质量，不缩尺寸")
    parser.add_argument("--recursive", action="store_true", help="递归扫描目录")
    parser.add_argument("--overwrite", action="store_true", help="覆盖同名输出")
    parser.add_argument("--skip-smaller", action="store_true", help="源图已小于目标时只复制到输出")
    parser.add_argument("--dry-run", action="store_true", help="只分析，不写文件")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--quiet", action="store_true", help="只输出结果路径")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")
    args = parse_args(argv)
    inputs = expand_inputs(args.inputs, args.recursive)
    if not inputs:
        print("未找到支持的图片文件。", file=sys.stderr)
        return 2
    if args.min_quality < 1 or args.min_quality > 95:
        print("--min-quality 必须在 1 到 95 之间。", file=sys.stderr)
        return 2
    if args.request_limit == 0:
        args.request_limit = None
    results: list[Result] = []
    failures: list[dict[str, str]] = []
    for source in inputs:
        fmt = infer_format(source, args.format, args.keep_format)
        try:
            single = len(inputs) == 1
            result = compress_one(
                source=source,
                output=args.output if single else None,
                output_dir=args.output_dir,
                fmt=fmt,
                target_bytes=args.max_bytes,
                request_limit=args.request_limit,
                max_edge=args.max_edge,
                min_quality=args.min_quality,
                no_resize=args.no_resize,
                overwrite=args.overwrite,
                skip_smaller=args.skip_smaller,
                dry_run=args.dry_run,
            )
            results.append(result)
            if not args.quiet and not args.json:
                status = "已是目标体积以下" if result.skipped else _result_line(result)
                print(status)
        except (CompressionError, OSError, UnidentifiedImageError, ValueError) as exc:
            failures.append({"source": str(source), "error": str(exc)})
            if not args.json:
                print(f"失败: {source} - {exc}", file=sys.stderr)
    if args.dry_run and not args.json:
        print(f"分析完成：{len(inputs)} 个文件，未写入输出。")
    if args.json:
        payload = {
            "items": [_result_dict(item) for item in results],
            "failures": failures,
            "summary": {
                "total": len(inputs),
                "succeeded": len(results),
                "failed": len(failures),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif not args.quiet:
        total_in = sum(item.source_bytes for item in results)
        total_out = sum(item.output_bytes for item in results)
        print(f"\n完成 {len(results)}/{len(inputs)} 张：{human_bytes(total_in)} -> {human_bytes(total_out)}")
    else:
        for item in results:
            print(item.output)
    return 0 if not failures else 1


def _result_line(result: Result) -> str:
    quality = f" q={result.quality}" if result.quality else ""
    return (
        f"{result.source.name} -> {result.output} | "
        f"{human_bytes(result.source_bytes)} -> {human_bytes(result.output_bytes)} | "
        f"{result.width}x{result.height} {result.format}{quality} | "
        f"请求体约 {human_bytes(result.request_bytes)}"
    )


def _result_dict(result: Result) -> dict:
    return {
        "source": str(result.source),
        "output": str(result.output),
        "source_bytes": result.source_bytes,
        "output_bytes": result.output_bytes,
        "width": result.width,
        "height": result.height,
        "format": result.format,
        "quality": result.quality,
        "request_bytes": result.request_bytes,
        "skipped": result.skipped,
    }


if __name__ == "__main__":
    raise SystemExit(main())
