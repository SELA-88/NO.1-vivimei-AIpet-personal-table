import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.build_clean_release import (
    API_KEY_PLACEHOLDER,
    build_release,
    create_zip,
    main,
)


class BuildCleanReleaseTests(unittest.TestCase):
    def test_build_release_replaces_key_and_removes_personal_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            target = root / "release"

            for relative in (
                "oc.py",
                "vivimei/oc.py",
            ):
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    'DEEPSEEK_API_KEY = "sk-package-test-secret"\n'
                    'DEEPSEEK_BASE_URL = "https://api.example.invalid/v1"\n'
                    'DEEPSEEK_MODEL = "example-model"\n',
                    encoding="utf-8",
                )

            (source / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (source / "assets").mkdir()
            (source / "assets" / "app_icon.icns").write_bytes(b"icon")
            launcher = source / "启动桌宠.app" / "Contents" / "MacOS" / "launch"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
            launcher.chmod(0o755)

            data = source / "data"
            (data / "chat_history").mkdir(parents=True)
            (data / "chat_history" / "2026-10-28.json").write_text(
                '[{"role":"user","content":"private chat"}]',
                encoding="utf-8",
            )
            (data / "chat_memory").mkdir()
            (data / "chat_memory" / "summary.md").write_text(
                "private memory",
                encoding="utf-8",
            )
            (data / "settings.json").write_text(
                '{"gender":"女","scale":0.8}',
                encoding="utf-8",
            )
            (data / "emotion_history.json").write_text(
                '{"version":1,"days":{"2026-10-28":{"开心":5}}}',
                encoding="utf-8",
            )
            storage = data / "workbench_data" / "storage"
            storage.mkdir(parents=True)
            (storage / "History").write_bytes(b"private browser history")

            (source / ".venv").mkdir()
            (source / ".venv" / "secret.txt").write_text(
                "environment",
                encoding="utf-8",
            )
            (source / ".DS_Store").write_bytes(b"metadata")
            (source / ".vivimei.lock").write_text("123", encoding="utf-8")

            release = build_release(source, target)

            for relative in ("oc.py", "vivimei/oc.py"):
                content = (release / relative).read_text(encoding="utf-8")
                self.assertIn(
                    f'DEEPSEEK_API_KEY = "{API_KEY_PLACEHOLDER}"',
                    content,
                )
                self.assertIn('DEEPSEEK_BASE_URL = "XXXXXXXXXX"', content)
                self.assertIn('DEEPSEEK_MODEL = "XXXXXXXXXX"', content)
                self.assertNotIn("sk-package-test-secret", content)
                self.assertNotIn("https://api.example.invalid/v1", content)
                self.assertNotIn("example-model", content)


            settings = json.loads(
                (release / "data" / "settings.json").read_text(encoding="utf-8")
            )
            self.assertEqual("未知", settings["gender"])
            self.assertEqual(
                {"version": 1, "days": {}},
                json.loads(
                    (release / "data" / "emotion_history.json").read_text(
                        encoding="utf-8"
                    )
                ),
            )
            self.assertEqual([], list((release / "data" / "chat_history").iterdir()))
            self.assertEqual([], list((release / "data" / "chat_memory").iterdir()))
            self.assertEqual(
                [],
                list((release / "data" / "workbench_data").iterdir()),
            )
            self.assertFalse((release / ".venv").exists())
            self.assertFalse((release / ".DS_Store").exists())
            self.assertFalse((release / ".vivimei.lock").exists())
            self.assertTrue((release / "main.py").is_file())
            self.assertTrue(
                (release / "启动桌宠.app" / "Contents" / "MacOS" / "launch").is_file()
            )

            packaged_bytes = b"".join(
                path.read_bytes()
                for path in release.rglob("*")
                if path.is_file()
            )
            self.assertNotIn(b"sk-package-test-secret", packaged_bytes)
            self.assertNotIn(b"private chat", packaged_bytes)
            self.assertNotIn(b"private memory", packaged_bytes)
            self.assertNotIn(b"private browser history", packaged_bytes)

    def test_create_zip_keeps_empty_data_directories_and_executable_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "oc.py").write_text(
                'DEEPSEEK_API_KEY = "sk-package-test-secret"\n'
                'DEEPSEEK_BASE_URL = "https://api.example.invalid/v1"\n'
                'DEEPSEEK_MODEL = "example-model"\n',
                encoding="utf-8",
            )
            launcher = source / "启动桌宠.app" / "Contents" / "MacOS" / "launch"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("#!/bin/zsh\n", encoding="utf-8")
            launcher.chmod(0o755)

            release = build_release(source, root / "维维美-隐私清理版")
            archive = create_zip(release, root / "维维美-隐私清理版.zip")

            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())
                self.assertIn(
                    "维维美-隐私清理版/data/chat_history/",
                    names,
                )
                self.assertIn(
                    "维维美-隐私清理版/data/chat_memory/",
                    names,
                )
                self.assertIn(
                    "维维美-隐私清理版/data/workbench_data/",
                    names,
                )
                info = package.getinfo(
                    "维维美-隐私清理版/启动桌宠.app/Contents/MacOS/launch"
                )
                self.assertTrue(((info.external_attr >> 16) & 0o111))
                self.assertNotIn(b"sk-package-test-secret", archive.read_bytes())

    def test_release_instructions_explain_placeholder_and_clean_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "oc.py").write_text(
                'DEEPSEEK_API_KEY = "sk-package-test-secret"\n'
                'DEEPSEEK_BASE_URL = "https://api.example.invalid/v1"\n'
                'DEEPSEEK_MODEL = "example-model"\n',
                encoding="utf-8",
            )
            (source / "使用说明.txt").write_text(
                "六、隐私\n"
                "项目包含本机接口配置，不要分享。\n"
                "七、运行要求\n"
                "macOS\n",
                encoding="utf-8",
            )

            release = build_release(source, root / "release")
            instructions = (release / "使用说明.txt").read_text(encoding="utf-8")

            self.assertIn(API_KEY_PLACEHOLDER, instructions)
            self.assertIn("DEEPSEEK_BASE_URL", instructions)
            self.assertIn("DEEPSEEK_MODEL", instructions)
            self.assertIn("vivimei/oc.py", instructions)
            self.assertIn("空数据模板", instructions)
            self.assertNotIn("项目包含本机接口配置，不要分享。", instructions)

    def test_main_builds_release_directory_and_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "main.py").write_text("print('ok')\n", encoding="utf-8")
            package_module = source / "vivimei"
            package_module.mkdir()
            (package_module / "oc.py").write_text(
                'DEEPSEEK_API_KEY = "sk-package-test-secret"\n'
                'DEEPSEEK_BASE_URL = "https://api.example.invalid/v1"\n'
                'DEEPSEEK_MODEL = "example-model"\n',
                encoding="utf-8",
            )
            release_root = root / "release"

            result = main([
                "--source",
                str(source),
                "--release-root",
                str(release_root),
            ])

            self.assertTrue((result.release_dir / "main.py").is_file())
            self.assertTrue(result.archive.is_file())


if __name__ == "__main__":
    unittest.main()
