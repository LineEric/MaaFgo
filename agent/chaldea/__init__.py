"""
Chaldea 队伍数据转换包

将 chaldea.center 的队伍分享数据转换为 BBchannel 可识别的战斗配置 JSON。
"""

import json
import os
import re
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
        # 缓存更新后刷新本地队伍下拉 option, MXU 重启后下拉可选
        _update_local_team_option()
    except Exception as e:
        logger.warning(f"[Chaldea] 保存缓存失败({name}): {e}")


# 本地队伍下拉 option 输出路径（位于安装目录的 options/ 下, 与 assets/options 同构）
_OPTION_OUTPUT = os.path.join("options", "本地队伍选择.json")


def _team_case_override(stem: str) -> dict:
    """构建"选中某本地队伍"case 的 pipeline_override, 写入缓存文件名。"""
    return {
        "原生自动战斗入口": {
            "action": {
                "type": "Custom",
                "param": {
                    "custom_action": "auto_battle",
                    "custom_action_param": {
                        "chaldea_import_source": stem,
                        "max_turns": 20,
                    },
                },
            }
        },
        "原生自动战斗_多次入口": {
            "action": {
                "type": "Custom",
                "param": {
                    "custom_action": "auto_battle_repeat",
                    "custom_action_param": {
                        "chaldea_import_source": stem,
                        "max_turns": 20,
                    },
                },
            }
        },
        "执行自动编队": {
            "attach": {"chaldea_import_source": stem}
        },
    }


def _update_local_team_option() -> None:
    """扫描 config/Battle 缓存, 重新生成"本地队伍选择" select option。

    option 的第一个 case 为"手动输入"(嵌套原导入输入框), 后续 case 为
    每个缓存队伍。选中本地队伍时通过 pipeline_override 把缓存文件名写入
    chaldea_import_source, agent 侧按本地文件加载。
    MXU 在下次启动时读取 options 目录即可在下拉中展示。
    """
    try:
        import glob

        cases = []
        if os.path.isdir(CACHE_DIR):
            for path in sorted(glob.glob(os.path.join(CACHE_DIR, "*.json"))):
                fname = os.path.basename(path)
                stem = fname[:-5] if fname.endswith(".json") else fname
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data.get("team"), dict):
                        continue
                    quest_id = (data.get("quest") or {}).get("id", "")
                except Exception:
                    continue
                # case name: 文件名去非法字符
                case_name = re.sub(r'[\\/:*?"<>| ]', "_", stem)
                label = f"{quest_id} | {stem}" if quest_id else stem
                cases.append((case_name, label, stem))

        option = {
            "option": {
                "本地队伍选择": {
                    "type": "select",
                    "label": "Chaldea 队伍选择",
                    "description": "选择本地缓存的 Chaldea 队伍，或选\"手动输入\"填分享链接/队伍ID/关卡ID/本地文件。下载过队伍后重启界面即可出现在下拉中。",
                    "default": "manual",
                    "cases": [
                        {
                            "name": "manual",
                            "label": "✍ 手动输入（链接 / 队伍ID / 关卡ID / 文件）",
                            "option": ["Chaldea导入手动输入"],
                        }
                    ]
                    + [
                        {
                            "name": cn,
                            "label": lb,
                            "pipeline_override": _team_case_override(stem),
                        }
                        for cn, lb, stem in cases
                    ],
                },
                # 手动输入子 option: 与"本地队伍选择"同文件生成, 保持同步
                "Chaldea导入手动输入": {
                    "type": "input",
                    "label": "手动导入（可选）",
                    "description": "填写 Chaldea 的分享链接、队伍ID、关卡ID，或点击浏览按钮从本地选择队伍 JSON 文件。留空则使用默认策略。",
                    "inputs": [
                        {
                            "name": "chaldea_import_source",
                            "label": "分享链接 / 队伍ID / 关卡ID / 本地文件（留空=默认策略）",
                            "default": "",
                            "verify": ".*",
                            "input_type": "file",
                        }
                    ],
                    "pipeline_override": {
                        "原生自动战斗入口": {
                            "action": {
                                "type": "Custom",
                                "param": {
                                    "custom_action": "auto_battle",
                                    "custom_action_param": {
                                        "chaldea_import_source": "{chaldea_import_source}",
                                        "max_turns": 20,
                                    },
                                },
                            }
                        },
                        "原生自动战斗_多次入口": {
                            "action": {
                                "type": "Custom",
                                "param": {
                                    "custom_action": "auto_battle_repeat",
                                    "custom_action_param": {
                                        "chaldea_import_source": "{chaldea_import_source}",
                                        "max_turns": 20,
                                    },
                                },
                            }
                        },
                        "执行自动编队": {
                            "attach": {"chaldea_import_source": "{chaldea_import_source}"}
                        },
                    },
                },
            }
        }

        os.makedirs(os.path.dirname(_OPTION_OUTPUT) or ".", exist_ok=True)
        with open(_OPTION_OUTPUT, "w", encoding="utf-8") as f:
            json.dump(option, f, ensure_ascii=False, indent=4)
        mfaalog.info(f"[Chaldea] 已生成本地队伍下拉 option: {_OPTION_OUTPUT} ({len(cases)}个队伍)")
    except Exception as e:
        logger.warning(f"[Chaldea] 生成本地队伍option失败: {e}")


def _load_local_file(source: str) -> Optional[dict]:
    """尝试把输入当作本地文件路径/文件名加载 BattleShareData。

    支持:
    - 绝对/相对路径: G:/xxx/team.json / ./team.json
    - 纯文件名: 自动在 CACHE_DIR (config/Battle) 中查找
    - .json 后缀可省略
    返回加载成功的 dict, 不是文件输入返回 None。
    """
    s = source.strip()
    if not s or re.match(r'^https?://', s) or s.isdigit() or s.startswith("G") and len(s) < 100 and not s.lower().endswith(".json"):
        return None

    candidates = []
    if os.sep in s or "/" in s or s.lower().endswith(".json") or s.startswith("."):
        # 显式路径形式
        candidates.append(s)
        if not s.lower().endswith(".json"):
            candidates.append(s + ".json")
    else:
        # 纯文件名: 在缓存目录中查找
        for name in (s, s + ".json"):
            candidates.append(os.path.join(CACHE_DIR, name))

    for path in candidates:
        if os.path.isfile(path):
            data = _load_cache_file(path)
            if data and isinstance(data.get("team"), dict):
                return data
    return None


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

    # 优先尝试本地文件输入（路径 / config/Battle 下的文件名）
    if quest_id is None and team_id is None and direct_data is None:
        local = _load_local_file(source)
        if local:
            mfaalog.info(f"[Chaldea] 输入识别为本地文件, 直接加载")
            quest_id = (local.get("quest") or {}).get("id", "0")
            team_id = "local"
            return local, quest_id, team_id

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
