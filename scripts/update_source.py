
import json
import plistlib
import re
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# 上游项目
REPO = "Yoroin/GlobalRefresh-PiP"

# 订阅源文件
OUTPUT = Path("GlobalRefresh-PiP-AltStore-Source.json")

# 上次成功更新记录
STATE = Path("globalrefresh-release-state.json")

# GitHub 最新正式版 API
API = f"https://api.github.com/repos/{REPO}/releases/latest"

HEADERS = {
    "User-Agent": "heyyypan-iOS-App-Sources",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def request(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def get_latest_release():
    release = json.loads(request(API).decode("utf-8"))

    if release.get("draft") or release.get("prerelease"):
        raise RuntimeError("不是正式版，停止更新。")

    if not release.get("id"):
        raise RuntimeError("Release ID 不存在。")

    return release


def find_ipa(release):
    assets = [
        item for item in release.get("assets", [])
        if item.get("name", "").lower().endswith(".ipa")
    ]

    if not assets:
        raise RuntimeError("最新 Release 没有 IPA，保留旧 JSON。")

    # 优先选择 unsigned IPA
    assets.sort(
        key=lambda item: (
            "unsigned" in item["name"].lower(),
            item.get("size", 0)
        ),
        reverse=True
    )

    return assets[0]


def load_state():
    if not STATE.exists():
        return {}

    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def inspect_ipa(ipa_path):
    with zipfile.ZipFile(ipa_path) as archive:
        plist_files = [
            name for name in archive.namelist()
            if re.fullmatch(
                r"Payload/[^/]+\.app/Info\.plist",
                name
            )
        ]

        if not plist_files:
            raise RuntimeError("IPA 内找不到 Info.plist。")

        info = plistlib.loads(archive.read(plist_files[0]))

    required = [
        "CFBundleIdentifier",
        "CFBundleShortVersionString",
        "CFBundleVersion"
    ]

    for key in required:
        if not info.get(key):
            raise RuntimeError(f"IPA 缺少字段：{key}")

    return info


def atomic_write(path, data):
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8"
    )
    temp_path.replace(path)


def main():
    print("正在检查 GlobalRefresh 最新正式版...")

    release = get_latest_release()
    asset = find_ipa(release)

    # 比较 Release ID 和 IPA 资源 ID
    current_state = {
        "release_id": release["id"],
        "tag_name": release.get("tag_name", ""),
        "asset_id": asset["id"],
        "asset_updated_at": asset.get("updated_at", "")
    }

    old_state = load_state()

    if old_state == current_state and OUTPUT.exists():
        print("没有新版本，也没有新的 IPA 资源。")
        print("跳过下载，JSON 保持不变。")
        return

    print("发现新版本或 IPA 资源发生变化！")
    print("Release：", release.get("tag_name"))
    print("IPA：", asset["name"])

    with tempfile.TemporaryDirectory() as temp:
        ipa_path = Path(temp) / asset["name"]

        print("正在下载 IPA...")
        ipa_path.write_bytes(
            request(asset["browser_download_url"])
        )

        info = inspect_ipa(ipa_path)

    version = str(info["CFBundleShortVersionString"])
    build = str(info["CFBundleVersion"])
    bundle_id = str(info["CFBundleIdentifier"])

    published = (
        release.get("published_at")
        or datetime.now(timezone.utc).isoformat()
    )

    changelog = (
        release.get("body")
        or f"GlobalRefresh {version} 正式版更新"
    )

    version_data = {
        "version": version,
        "buildVersion": build,
        "date": published,
        "downloadURL": asset["browser_download_url"],
        "size": asset["size"],
        "localizedDescription": changelog
    }

    minimum_os = (
        info.get("MinimumOSVersion")
        or info.get("LSMinimumSystemVersion")
    )

    if minimum_os:
        version_data["minOSVersion"] = str(minimum_os)

    app = {
        "name": "全局高刷",
        "iconURL": "https://raw.githubusercontent.com/heyyypan/iOS-App-Sources/main/assets/globalrefresh-icon.png",
        "bundleIdentifier": bundle_id,
        "developerName": "Yoroin",
        "subtitle": "全局高刷悬浮窗",
        "localizedDescription": (
            "基于 iOS 画中画的悬浮窗工具，"
            "可辅助部分 ProMotion 设备获得更高刷新率表现。"
        ),
        "developerWebsite": (
            "https://github.com/Yoroin/GlobalRefresh-PiP"
        ),
        "versions": [version_data]
    }

    source = {
        "name": "全局高刷",
        "identifier": "com.heyyypan.ios-app-sources",
        "subtitle": "全局高刷自动更新源",
        "description": (
            "自动跟踪 Yoroin/GlobalRefresh-PiP 最新正式版。"
        ),
        "website": (
            "https://github.com/heyyypan/iOS-App-Sources"
        ),
        "apps": [app],
        "news": []
    }

    # 先成功生成订阅源，再保存更新记录
    atomic_write(OUTPUT, source)
    atomic_write(STATE, current_state)

    print("订阅源更新成功！")
    print("版本：", version)
    print("Build：", build)
    print("Bundle ID：", bundle_id)
    print("Release ID：", release["id"])


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("更新失败：", error, file=sys.stderr)
        sys.exit(1)
