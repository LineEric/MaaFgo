#!/usr/bin/env python
"""
MaaMCP launcher — 修复 Python 3.14 Path.mkdir 回归 + 重定向数据目录到项目内。

用项目内 .maa_mcp_data/ 替代被锁住的系统 MaaMCP 数据目录，
避免 FileExistsError 导致 MaaMCP 无法启动。
"""
import os
import sys

# ---- patch 1: 重定向数据目录到项目内干净位置 ----
PROJECT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(PROJECT, ".maa_mcp_data")
os.makedirs(DATA_DIR, exist_ok=True)

import maa_mcp.paths as p

_orig_get_data_dir = p.get_data_dir
p.get_data_dir = lambda: type(_orig_get_data_dir())(DATA_DIR)

# 清空可能的缓存
try:
    import platformdirs
    if hasattr(platformdirs, "users"):
        # platformdirs 可能有模块级缓存
        pass
except ImportError:
    pass

# 确保所有子目录存在
for d in (p.get_resource_dir(), p.get_model_dir(), p.get_ocr_dir(),
          p.get_screenshots_dir(), p.get_logs_dir()):
    d.mkdir(parents=True, exist_ok=True)

# ---- patch 2: 安全网，吞掉 Python 3.14 Path.mkdir 回归导致的 FileExistsError ----
_orig_os_mkdir = os.mkdir
def _safe_mkdir(path, mode=0o777, *, dir_fd=None):
    try:
        return _orig_os_mkdir(path, mode, dir_fd=dir_fd)
    except FileExistsError:
        pass
os.mkdir = _safe_mkdir

# ---- 启动 MaaMCP stdio server ----
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="maa_mcp")

from maa_mcp.main import mcp

# 以 stdio 模式启动 MCP server
if __name__ == "__main__":
    mcp.run()
