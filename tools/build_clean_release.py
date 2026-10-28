import argparse
import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipInfo


@dataclass(frozen=True)
class BuildResult:
    release_dir: Path
    archive: Path

API_KEY_PLACEHOLDER = "XXXXXXXXXXXXXXXXXXX"
BASE_URL_PLACEHOLDER = "XXXXXXXXXX"
MODEL_PLACEHOLDER = "XXXXXXXXXX"
COPY_ITEMS = (
    "assets",
    "main.py",
    "README.md",
    "oc.py",
    "requirements.txt",
    "vivimei",
    "workbench",
    "使用说明.txt",
    "启动桌宠.app",
    "安装依赖.app",
)
DEFAULT_SETTINGS = {
    "scale": 0.35,
    "keyboard_control": False,
    "gender": "未知",
    "free_activity": False,
    "fall_mode": "smooth",
}
DEFAULT_EMOTION_HISTORY = {"version": 1, "days": {}}
SKIP_NAMES = {".DS_Store", "__pycache__"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
API_KEY_LINE = re.compile(
    r'(?m)^DEEPSEEK_API_KEY\s*=\s*["\'][^"\']*["\']\s*$'
)
BASE_URL_LINE = re.compile(
    r'(?m)^DEEPSEEK_BASE_URL\s*=\s*["\'][^"\']*["\']\s*$'
)
MODEL_LINE = re.compile(
    r'(?m)^DEEPSEEK_MODEL\s*=\s*["\'][^"\']*["\']\s*$'
)
API_KEY_PATTERN = re.compile(rb"sk-[A-Za-z0-9_-]{16,}")
PRIVACY_SECTION = re.compile(
    r"(?ms)^六、隐私\s*\n.*?(?=^七、运行要求\s*$)"
)
RELEASE_PRIVACY_SECTION = "\n".join(
    (
        "六、隐私",
        "聊天内容会在你配置 DeepSeek API 后发送到 DeepSeek。",
        f"发布包中的 API Key 已替换为 {API_KEY_PLACEHOLDER}。",
        "请只在你自己的副本中于 vivimei/oc.py 填入 API Key、DEEPSEEK_BASE_URL 和 DEEPSEEK_MODEL。",
        f"发布包已将这三项分别替换为 {API_KEY_PLACEHOLDER}、{BASE_URL_PLACEHOLDER} 和 {MODEL_PLACEHOLDER}。",
        "不要分享填写后的文件。",
        "data 目录中的聊天、情绪和工作台内容都是本地个人数据。",
        "发布包只提供空数据模板，不包含原使用者的数据。",
        "",
    )
)


def _ignore_unwanted(_directory, names):
    return {
        name
        for name in names
        if name in SKIP_NAMES or Path(name).suffix in SKIP_SUFFIXES
    }


def _sanitize_api_configuration(path):
    content = path.read_text(encoding="utf-8")
    replacements = (
        (
            API_KEY_LINE,
            f'DEEPSEEK_API_KEY = "{API_KEY_PLACEHOLDER}"',
        ),
        (
            BASE_URL_LINE,
            f'DEEPSEEK_BASE_URL = "{BASE_URL_PLACEHOLDER}"',
        ),
        (
            MODEL_LINE,
            f'DEEPSEEK_MODEL = "{MODEL_PLACEHOLDER}"',
        ),
    )
    for pattern, replacement in replacements:
        content, count = pattern.subn(replacement, content)
        if count != 1:
            raise ValueError(f"API configuration line not found: {path}")
    path.write_text(content, encoding="utf-8")


def _write_json(path, payload):
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sanitize_instructions(path):
    content = path.read_text(encoding="utf-8")
    sanitized, count = PRIVACY_SECTION.subn(RELEASE_PRIVACY_SECTION, content)
    if count != 1:
        raise ValueError(f"Privacy section not found in instructions: {path}")
    path.write_text(sanitized, encoding="utf-8")


def _create_empty_data(release):
    data = release / "data"
    for relative in ("chat_history", "chat_memory", "workbench_data"):
        (data / relative).mkdir(parents=True, exist_ok=True)
    _write_json(data / "settings.json", DEFAULT_SETTINGS)
    _write_json(data / "emotion_history.json", DEFAULT_EMOTION_HISTORY)


def _audit_release(release):
    for path in release.rglob("*"):
        if not path.is_file():
            continue
        if API_KEY_PATTERN.search(path.read_bytes()):
            raise ValueError(f"Possible API key leaked: {path.relative_to(release)}")


def build_release(source_dir, release_dir):
    source = Path(source_dir).resolve()
    release = Path(release_dir).resolve()
    if release.exists():
        raise FileExistsError(f"Release directory already exists: {release}")

    release.mkdir(parents=True)
    for relative in COPY_ITEMS:
        source_path = source / relative
        if not source_path.exists():
            continue
        destination = release / relative
        if source_path.is_dir():
            shutil.copytree(
                source_path,
                destination,
                ignore=_ignore_unwanted,
                copy_function=shutil.copy2,
            )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)

    for relative in ("oc.py", "vivimei/oc.py"):
        path = release / relative
        if path.is_file():
            _sanitize_api_configuration(path)

    instructions = release / "使用说明.txt"
    if instructions.is_file():
        _sanitize_instructions(instructions)

    _create_empty_data(release)
    _audit_release(release)
    return release


def create_zip(release_dir, archive_path):
    release = Path(release_dir).resolve()
    archive = Path(archive_path).resolve()
    if archive.exists():
        raise FileExistsError(f"Archive already exists: {archive}")

    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for path in sorted(release.rglob("*")):
            relative = path.relative_to(release).as_posix()
            archive_name = f"{release.name}/{relative}"
            if path.is_dir():
                info = ZipInfo(f"{archive_name}/")
                info.create_system = 3
                info.external_attr = (
                    (path.stat().st_mode & 0xFFFF) << 16
                ) | 0x10
                package.writestr(info, b"")
            elif path.is_file():
                package.write(path, archive_name)
    return archive


def main(argv=None):
    default_source = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="Build a privacy-cleaned release")
    parser.add_argument("--source", type=Path, default=default_source)
    parser.add_argument("--release-root", type=Path)
    parser.add_argument("--name", default="维维美-隐私清理版")
    args = parser.parse_args(argv)

    source = args.source.resolve()
    release_root = (args.release_root or source / "release").resolve()
    if Path(args.name).name != args.name:
        parser.error("--name must not contain a path separator")

    release_dir = build_release(source, release_root / args.name)
    archive = create_zip(release_dir, release_root / f"{args.name}.zip")
    return BuildResult(release_dir=release_dir, archive=archive)


if __name__ == "__main__":
    result = main()
    print(f"Release: {result.release_dir}")
    print(f"Archive: {result.archive}")
