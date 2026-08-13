"""
BBC 进程识别工具

用于精确识别 BBchannel 进程，避免误杀无关进程。

重要：绝不能使用 `'BBchannel' in arg` 这类子串匹配 —— 当 MaaFgo 安装在
目录名含 "BBchannel" 的路径下（如 D:\\BBchannel\\...）时，agent 自身的
python.exe 与 MaaFgo GUI 的 msedgewebview2.exe 的命令行里都会包含
"BBchannel" 子串，会被误判成 BBC 进程而杀死，导致 agent / 界面闪退。
因此这里只按"可执行文件名"做精确匹配。
"""

import os

# BBC 可执行文件名（统一小写）。仅匹配文件名，绝不匹配路径。
_BBC_EXE_NAMES = {
    "bbchannel.exe",
    "bbchannel64.exe",
    "bbchannel_debug.exe",
    "bbchannel64_debug.exe",
}


def _exe_name_from_path(path) -> str:
    """取路径末尾的可执行文件名并转小写（Windows 文件名大小写不敏感）"""
    return os.path.basename((path or "").lower())


def is_bbc_process(name="", cmdline=None) -> bool:
    """判断进程是否为 BBC 进程。

    :param name: psutil 进程名（proc.info.get('name')，即 exe 文件名）
    :param cmdline: 命令行参数列表（proc.info.get('cmdline')）
    """
    # 1) 进程名直接精确匹配
    if _exe_name_from_path(name) in _BBC_EXE_NAMES:
        return True

    # 2) 命令行第一个参数 argv[0]（通常是 exe 完整路径）的 basename 精确匹配
    if cmdline:
        argv0 = cmdline[0] if isinstance(cmdline, (list, tuple)) else None
        if argv0 and _exe_name_from_path(argv0) in _BBC_EXE_NAMES:
            return True

    return False
