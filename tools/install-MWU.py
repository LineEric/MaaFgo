from pathlib import Path

import shutil
import sys
import subprocess
import os
import urllib.request
import json

from configure import configure_ocr_model


working_dir = Path(__file__).parent.parent.resolve()
install_path = working_dir / Path("build")
version = len(sys.argv) > 1 and sys.argv[1] or "v0.0.1"

# the first parameter is self name
if sys.argv.__len__() < 4:
    print("Usage: python install-MWU.py <version> <os> <arch>")
    print("Example: python install-MWU.py v1.0.0 win x86_64")
    sys.exit(1)

os_name = sys.argv[2]
arch = sys.argv[3]


def get_dotnet_platform_tag():
    """自动检测当前平台并返回对应的dotnet平台标签"""
    if os_name == "win" and arch == "x86_64":
        platform_tag = "win-x64"
    elif os_name == "win" and arch == "aarch64":
        platform_tag = "win-arm64"
    elif os_name == "macos" and arch == "x86_64":
        platform_tag = "osx-x64"
    elif os_name == "macos" and arch == "aarch64":
        platform_tag = "osx-arm64"
    elif os_name == "linux" and arch == "x86_64":
        platform_tag = "linux-x64"
    elif os_name == "linux" and arch == "aarch64":
        platform_tag = "linux-arm64"
    else:
        print("Unsupported OS or architecture.")
        print("available parameters:")
        print("version: e.g., v1.0.0")
        print("os: [win, macos, linux, android]")
        print("arch: [aarch64, x86_64]")
        sys.exit(1)

    return platform_tag


def install_deps():
    if not (working_dir / "deps" / "bin").exists():
        print('Please download the MaaFramework to "deps" first.')
        sys.exit(1)

    if os_name == "android":
        shutil.copytree(
            working_dir / "deps" / "bin",
            install_path,
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "share" / "MaaAgentBinary",
            install_path / "MaaAgentBinary",
            dirs_exist_ok=True,
        )
    else:
        shutil.copytree(
            working_dir / "deps" / "bin",
            install_path / "runtimes" / get_dotnet_platform_tag() / "native",
            ignore=shutil.ignore_patterns(
                "*MaaDbgControlUnit*",
                "*MaaThriftControlUnit*",
                "*MaaRpc*",
                "*MaaHttp*",
                "plugins",
                "*.node",
                "*MaaPiCli*",
            ),
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "share" / "MaaAgentBinary",
            install_path / "libs" / "MaaAgentBinary",
            dirs_exist_ok=True,
        )
        shutil.copytree(
            working_dir / "deps" / "bin" / "plugins",
            install_path / "plugins" / get_dotnet_platform_tag(),
            dirs_exist_ok=True,
        )


def install_resource():

    configure_ocr_model()

    shutil.copytree(
        working_dir / "assets" / "resource",
        install_path / "resource",
        dirs_exist_ok=True,
    )
    shutil.copy2(
        working_dir / "assets" / "interface.json",
        install_path,
    )

    # Copy options and i18n directories
    if (working_dir / "assets" / "options").exists():
        shutil.copytree(
            working_dir / "assets" / "options",
            install_path / "options",
            dirs_exist_ok=True,
        )
    if (working_dir / "assets" / "i18n").exists():
        shutil.copytree(
            working_dir / "assets" / "i18n",
            install_path / "i18n",
            dirs_exist_ok=True,
        )

    with open(install_path / "interface.json", "r", encoding="utf-8") as f:
        interface = json.load(f)

    interface["version"] = version

    # 设置 agent 使用内置解释器（黑魔法模式下，MWU 会自动处理 python 调用）
    # 对于黑魔法，通常不需要指定具体的 python.exe 路径，或者指向一个占位符
    interface["agent"]["child_exec"] = "python"

    with open(install_path / "interface.json", "w", encoding="utf-8") as f:
        json.dump(interface, f, ensure_ascii=False, indent=2)


def install_chores():
    shutil.copy2(
        working_dir / "README.md",
        install_path,
    )
    shutil.copy2(
        working_dir / "LICENSE",
        install_path,
    )


def setup_deps_for_black_magic():
    """
    为 MWU 的黑魔法 Agent 机制准备 deps 目录
    将 cv2 和 numpy 安装到 build/deps，以便动态加载
    """
    deps_path = install_path / "deps"
    deps_path.mkdir(exist_ok=True)
    
    print(f"Installing extra dependencies for Agent to: {deps_path}")
    
    # 读取 pyproject.toml 中的 numpy 版本以确保 ABI 兼容
    # 这里为了简化，先硬编码为与 MWU 主程序匹配的版本
    target_numpy = "numpy==2.3.5" 
    
    try:
        subprocess.run([
            sys.executable, "-m", "pip", "install", 
            target_numpy,
            "opencv-python", 
            "--target", str(deps_path),
            "--no-cache-dir"
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("Successfully installed cv2 and numpy into deps folder.")
    except Exception as e:
        print(f"Warning: Failed to install deps: {e}")


def install_agent():
    # 复制 agent 目录
    shutil.copytree(
        working_dir / "agent",
        install_path / "agent",
        dirs_exist_ok=True,
    )


def install_bbcdll():
    """复制 bbcdll 目录"""
    shutil.copytree(
        working_dir / "bbcdll",
        install_path / "bbcdll",
        dirs_exist_ok=True,
    )


def install_tasks():
    """复制 tasks 目录"""
    if (working_dir / "assets" / "tasks").exists():
        shutil.copytree(
            working_dir / "assets" / "tasks",
            install_path / "tasks",
            dirs_exist_ok=True,
        )


if __name__ == "__main__":
    install_deps()
    install_resource()
    install_chores()
    setup_deps_for_black_magic()  # 替换为 deps 准备逻辑
    install_agent()
    install_bbcdll()
    install_tasks()

    print(f"Install to {install_path} successfully.")
