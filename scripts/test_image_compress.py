#!/usr/bin/env python3
"""image_compress.py 的回归测试。"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

SCRIPT = Path(__file__).with_name("image_compress.py")


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


class ImageCompressTests(unittest.TestCase):
    def test_photo_under_target_size(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "photo.png"
            output = root / "out"
            image = Image.effect_noise((1800, 1200), 80).convert("RGB")
            image.save(source)
            result = run_cli(
                str(source),
                "--max-bytes", "120KB",
                "--max-edge", "1000",
                "--output-dir", str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            compressed = next(output.iterdir())
            self.assertLessEqual(compressed.stat().st_size, 120 * 1024)
            with Image.open(compressed) as image:
                self.assertLessEqual(max(image.size), 1000)

    def test_alpha_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "alpha.png"
            output = root / "out"
            image = Image.new("RGBA", (900, 600), (255, 0, 0, 0))
            image.putpixel((450, 300), (0, 255, 0, 255))
            image.save(source)
            result = run_cli(
                str(source),
                "--format", "webp",
                "--max-bytes", "80KB",
                "--max-edge", "600",
                "--output-dir", str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            compressed = next(output.iterdir())
            with Image.open(compressed) as image:
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.getpixel((0, 0))[3], 0)

    def test_request_limit_fails_instead_of_writing_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "photo.png"
            output = root / "out"
            Image.effect_noise((1400, 900), 90).convert("RGB").save(source)
            result = run_cli(
                str(source),
                "--max-bytes", "300KB",
                "--request-limit", "100KB",
                "--output-dir", str(output),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("request-limit", result.stdout + result.stderr)
            self.assertFalse(output.exists())

    def test_dry_run_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "photo.png"
            output = root / "out"
            Image.new("RGB", (800, 600), "navy").save(source)
            result = run_cli(
                str(source),
                "--dry-run",
                "--output-dir", str(output),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(output.exists())

    def test_batch_and_unique_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir = root / "src"
            output = root / "out"
            source_dir.mkdir()
            Image.new("RGB", (500, 400), "red").save(source_dir / "a.png")
            Image.new("RGB", (600, 400), "blue").save(source_dir / "b.png")
            first = run_cli(str(source_dir), "--recursive", "--output-dir", str(output))
            second = run_cli(str(source_dir), "--recursive", "--output-dir", str(output))
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            names = sorted(item.name for item in output.iterdir())
            self.assertEqual(len(names), 4)

    def test_animation_frames_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "anim.gif"
            output = root / "out"
            frames = [
                Image.new("RGB", (240, 180), "red"),
                Image.new("RGB", (240, 180), "green"),
                Image.new("RGB", (240, 180), "blue"),
            ]
            frames[0].save(
                source,
                save_all=True,
                append_images=frames[1:],
                duration=200,
                loop=0,
            )
            for fmt in ("webp", "gif"):
                result = run_cli(
                    str(source),
                    "--format", fmt,
                    "--max-bytes", "80KB",
                    "--max-edge", "240",
                    "--output-dir", str(output / fmt),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                compressed = next((output / fmt).iterdir())
                with Image.open(compressed) as image:
                    self.assertEqual(image.n_frames, 3)


if __name__ == "__main__":
    unittest.main()
