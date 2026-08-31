"""
Chaldea 队伍数据转换包

将 chaldea.center 的队伍分享数据转换为 BBchannel 可识别的战斗配置 JSON。
"""

import json
import os
import logging
from typing import Optional

import mfaalog

from .chaldea_client import (
    fetch_teams_by_quest, fetch_team_by_id, select_best_team,
    decode_content, parse_import_source,
)
from .game_data import get_servant_name
from .bbc_formatter import chaldea_to_bbc
from .config_checker import validate_bbc_config

logger = logging.getLogger(__name__)

# 本地队伍缓存目录（相对 agent 运行的工作目录，即资源包根的 config/Battle）
CACHE_DIR = os.path.join("config", "Battle")


def _build_cache_name(share_data: dict, quest_id, team_id) -> str:
    """生成可读的缓存文件名: 关卡ID_队伍ID_从者名们

    例: 94149047_77210_太岁星君_大总统_阿蒂拉
    从者名查不到时用 svtId 兜底; 文件名非法字符替换为下划线。
    """
    names = []
    for svt in (share_data.get("team") or {}).get("onFieldSvts") or []:
        svt_id = svt.get("svtId")
        if svt_id is None:
            continue
        name = get_servant_name(svt_id)
        names.append(name)
    name_part = "_".join(names) if names else "unknown"
    # Windows 文件名非法字符过滤
    for ch in r'\/:*?"<>|':
        name_part = name_part.replace(ch, "_")
    # 限制总长度, 防止路径过长
    if len(name_part) > 80:
        name_part = name_part[:80]
    return f"{quest_id}_{team_id}_{name_part}"


def _find_cache_file(quest_id, team_id) -> Optional[str]:
    """在缓存目录中查找已存在的队伍缓存文件, 找不到返回 None

    兼容两类命名:
    - 新可读名: {quest_id}_{team_id}_从者名们.json (按前缀匹配)
    - 旧纯ID名: chaldea_team_{team_id}.json / chaldea_quest_{quest_id}_*.json
    team_id 已知但 quest_id 未知时, 用正则匹配 任意数字_{team_id}_ 前缀。
    """
    try:
        if not os.path.isdir(CACHE_DIR):
            return None
        import re
        patterns = []
        if team_id is not None:
            # 旧格式精确名
            patterns.append(("exact", f"chaldea_team_{team_id}"))
            # 新格式: quest_id 已知用确定前缀, 未知用通配
            if quest_id is not None:
                patterns.append(("prefix", f"{quest_id}_{team_id}_"))
            else:
                patterns.append(("regex", rf"^\d+_{team_id}_"))
        if quest_id is not None:
            patterns.append(("prefix", f"chaldea_quest_{quest_id}"))
        if not patterns:
            return None

        for fname in os.listdir(CACHE_DIR):
            if not fname.endswith(".json"):
                continue
            stem = fname[:-5]
            for mode, pat in patterns:
                if (mode == "exact" and stem == pat) or (
                    mode == "prefix" and stem.startswith(pat)
                ) or (mode == "regex" and re.match(pat, stem)):
                    return os.path.join(CACHE_DIR, fname)
    except Exception as e:
        logger.warning(f"[Chaldea] 扫描缓存目录失败: {e}")
    return None


def _cache_path(name: str) -> str:
    return os.path.join(CACHE_DIR, f"{name}.json")


def _load_cache_file(path: str) -> Optional[dict]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        mfaalog.info(f"[Chaldea] 命中本地缓存: {path}")
        return data
    except Exception as e:
        logger.warning(f"[Chaldea] 读取缓存失败({path}): {e}")
        return None


def _save_cache(name: str, data: dict) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _cache_path(name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        mfaalog.info(f"[Chaldea] 已保存队伍到本地缓存: {path}")
    except Exception as e:
        logger.warning(f"[Chaldea] 保存缓存失败({name}): {e}")


def fetch_share_data(source: str):
    """下载 + 解码 Chaldea BattleShareData（BBC 与 auto-battle 共用入口）。

    解析 source（team_id / quest_id / URL / data= 压缩串），按需从 Chaldea
    API 下载（team_id / quest_id）或离线解码（data= 内联数据），返回解码后的
    BattleShareData dict。

    Returns:
        (share_data, quest_id, team_id)：
        - share_data: 解码后的 dict，失败为 None
        - quest_id / team_id: 解析出的展示用标识（str），供命名等使用
    """
    quest_id, team_id, direct_data = parse_import_source(source)
    mfaalog.info(f"[Chaldea] parse_import_source: quest_id={quest_id} team_id={team_id} direct_data={'有' if direct_data else '无'}")
    share_data = None

    if direct_data:
        logger.info("[Chaldea] 匹配到长链接数据特征，开启离线解码...")
        mfaalog.info("[Chaldea] 离线解码 direct_data...")
        share_data = decode_content(direct_data)
        team_id = "offline"
        quest_id = (share_data.get("quest") or {}).get("id", "0") if share_data else "0"
    elif team_id:
        cache_file = _find_cache_file(None, team_id)
        share_data = None
        if cache_file:
            share_data = _load_cache_file(cache_file)
        if share_data:
            team_id = str(team_id)
            quest_id = (share_data.get("quest") or {}).get("id", "0")
            mfaalog.info(f"[Chaldea] 使用缓存队伍, 跳过下载: team_id={team_id}")
        else:
            mfaalog.info(f"[Chaldea] 按 team_id={team_id} 下载队伍...")
            team_resp = fetch_team_by_id(team_id)
            if team_resp and "content" in team_resp:
                mfaalog.info(f"[Chaldea] 队伍下载成功, 开始解码 content")
                share_data = decode_content(team_resp["content"])
                quest_id = team_resp.get("questId", "0")
                if share_data:
                    # 下载成功后落盘缓存, 下次不再重复下载
                    _save_cache(_build_cache_name(share_data, quest_id, team_id), share_data)
            else:
                logger.error("[Chaldea] 队伍接口无匹配数据。")
                mfaalog.error(f"[Chaldea] 队伍接口无匹配数据: team_id={team_id}")
    elif quest_id:
        cache_file = _find_cache_file(quest_id, None)
        share_data = None
        if cache_file:
            share_data = _load_cache_file(cache_file)
        if share_data:
            mfaalog.info(f"[Chaldea] 使用缓存队伍, 跳过搜索: quest_id={quest_id}")
            team_id = "cached"
        else:
            mfaalog.info(f"[Chaldea] 按 quest_id={quest_id} 搜索队伍...")
            teams = fetch_teams_by_quest(quest_id, 3, 10)
            mfaalog.info(f"[Chaldea] 获取到 {len(teams)} 个队伍")
            best = select_best_team(teams)
            if best and "content" in best:
                mfaalog.info(f"[Chaldea] 选择最佳队伍 id={best.get('id')}, 开始解码 content")
                share_data = decode_content(best["content"])
                team_id = best.get("id", "top")
                if share_data:
                    _save_cache(_build_cache_name(share_data, quest_id, team_id), share_data)
            else:
                logger.error("[Chaldea] 该关卡无可用队伍数据。")
                mfaalog.error(f"[Chaldea] 该关卡无可用队伍数据: quest_id={quest_id}")
    else:
        logger.error("[Chaldea] 无法解析输入来源。")
        mfaalog.error(f"[Chaldea] 无法解析输入来源: {source[:100]}")

    mfaalog.info(f"[Chaldea] fetch_share_data 完成: share_data={'有' if share_data else '无'} quest_id={quest_id} team_id={team_id}")

    return share_data, quest_id, team_id


def fetch_and_convert(source: str, output_dir: Optional[str] = None) -> Optional[str]:
    """
    主入口编排: 通过 source 获取数据并生成 BBC 配置文件

    参数:
        source: 用户输入 (quest_id / team_id / URL / 压缩数据)
        output_dir: 输出目录

    返回:
        保存的文件名，失败返回 None
    """
    share_data, quest_id, team_id = fetch_share_data(source)
    if not share_data:
        logger.error("[Chaldea] 数据结构提取失败。")
        return None

    bbc_config = chaldea_to_bbc(share_data)

    if not bbc_config:
        logger.error("[Chaldea] 转换结果为空。")
        return None

    filename = f"chaldea_{quest_id}_{team_id}.json"
    filepath = os.path.join(output_dir or ".", filename)

    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(bbc_config, f, ensure_ascii=False, indent=4)

    logger.info(f"[Chaldea] 已保存队伍 JSON 到 {filepath}")
    return filename
