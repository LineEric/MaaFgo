import json
import urllib.request
import urllib.parse
import ssl
import base64
import gzip
import logging
import re
import os
from typing import Optional, Dict

logger = logging.getLogger(__name__)

CHALDEA_API = "https://worker.chaldea.center/api/v4"

# 默认常规指令卡优先级策略 (从 BBC 配置文件中提取的经典保底策略)
DEFAULT_STRATEGY = [
    {
        "card1": {
            "type": 0,
            "cards": [1],
            "criticalStar": 0,
            "more_or_less": True
        },
        "card2": {
            "type": 1,
            "cards": ["1A", "1B", "1Q", "2B", "3B", "2A", "3A", "2Q", "3Q"],
            "criticalStar": 0,
            "more_or_less": True
        },
        "card3": {
            "type": 1,
            "cards": ["1A", "1B", "1Q", "2B", "3B", "2A", "3A", "2Q", "3Q"],
            "criticalStar": 0,
            "more_or_less": True
        },
        "breakpoint": [False, False],
        "colorFirst": True
    }
]

def fetch_teams_by_quest(quest_id: int, phase: int = 3, limit: int = 5) -> list:
    url = f"{CHALDEA_API}/quest/{quest_id}/team?phase={phase}&page=1&limit={limit}&free=true"
    logger.info(f"[Chaldea] 请求关卡队伍排行榜: {url}")
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("data", [])
    except Exception as e:
        logger.error(f"[Chaldea] 关卡API请求失败: {e}")
        return []

def fetch_team_by_id(team_id: int) -> Optional[dict]:
    url = f"{CHALDEA_API}/team/{team_id}"
    logger.info(f"[Chaldea] 请求单独队伍配置: {url}")
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})
        resp = urllib.request.urlopen(req, timeout=15, context=ctx)
        return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"[Chaldea] 队伍API请求失败: {e}")
        return None

def select_best_team(teams: list) -> Optional[dict]:
    if not teams:
        return None
    return max(
        teams,
        key=lambda t: t.get("votes", {}).get("up", 0) - t.get("votes", {}).get("down", 0)
    )

def decode_content(content: str) -> Optional[dict]:
    try:
        if content.startswith("G"):
            b64_data = content[1:]
        elif content.startswith("H4s"):
            b64_data = content
        else:
            logger.error(f"[Chaldea] 未知 content 格式: {content[:10]}")
            return None

        # 补齐 base64 padding
        padding = 4 - len(b64_data) % 4
        if padding != 4:
            b64_data += "=" * padding

        raw = base64.urlsafe_b64decode(b64_data)
        decompressed = gzip.decompress(raw)
        return json.loads(decompressed.decode("utf-8"))
    except Exception as e:
        logger.error(f"[Chaldea] content 解码失败: {e}")
        return None

def convert_actions_to_bbc_rounds(actions: list, delegate: dict = None) -> dict:
    rounds_config = {}
    current_skills = []
    current_nps = []
    round_idx = 1
    turn_idx = 0

    replace_members = delegate.get("replaceMemberIndexes", []) if delegate else []
    replace_ptr = 0

    for action in actions:
        action_type = action.get("type", "")

        if action_type == "skill":
            svt_idx = action.get("svt")       # None 为御主技能
            skill_idx = action.get("skill", 0)
            options = action.get("options", {})

            if svt_idx is None:
                # 御主技能
                bbc_skill_idx = 10 + skill_idx
                if bbc_skill_idx == 12: # 换人服，3技能 (索引2+10) = 12
                    # 从 delegate 取出换人信息并作为御主的 target 进行偏移映射写入
                    if replace_ptr < len(replace_members):
                        field_idx = replace_members[replace_ptr][0] + 1
                        backup_idx = replace_members[replace_ptr][1] + 1
                        current_skills.append([-2, backup_idx]) # 在 BBC 里换人目标特殊表示为 [-2, backup_idx]
                        replace_ptr += 1
                    else:
                        current_skills.append([-2, 1]) # fallback
                    continue
            else:
                # 从者技能 (0~2) * 3 + (0~2) + 1 = 1~9
                bbc_skill_idx = svt_idx * 3 + skill_idx + 1
            
            player_target = options.get("playerTarget")
            if player_target is not None and player_target > 0:
                current_skills.append([bbc_skill_idx, player_target + 1])
            else:
                current_skills.append(bbc_skill_idx)

        elif action_type == "attack":
            attacks = action.get("attacks", [])
            for atk in attacks:
                if atk.get("isTD", False):
                    svt_pos = atk.get("svt", 0) + 1
                    if svt_pos not in current_nps:
                        current_nps.append(svt_pos)

            # 一个 attack 即一个 Round/Turn 结束（BB频道以Round作为分割回合结构）
            rounds_config[f"round{round_idx}_turns"] = 1
            rounds_config[f"round{round_idx}_extraSkill"] = []
            rounds_config[f"round{round_idx}_turn{turn_idx}_skill"] = current_skills.copy()
            rounds_config[f"round{round_idx}_turn{turn_idx}_np"] = current_nps.copy()
            rounds_config[f"round{round_idx}_turn{turn_idx}_strategy"] = DEFAULT_STRATEGY
            rounds_config[f"round{round_idx}_turn{turn_idx}_condition"] = None
            
            current_skills = []
            current_nps = []
            round_idx += 1

    return rounds_config

def chaldea_to_bbc(share_data: dict) -> dict:
    team = share_data.get("team", {})
    actions = share_data.get("actions", [])
    delegate = share_data.get("delegate", {})
    
    # 构建基础模板
    result = {
        "_source": "chaldea",
        "_questId": (share_data.get("quest") or {}).get("id"),
        "_appBuild": share_data.get("appBuild"),
    }
    
    svts = list(team.get("onFieldSvts", [])) + list(team.get("backupSvts", []))
    for i in range(6):
        svt_info = svts[i] if i < len(svts) else None
        result[f"servant_{i}_name"] = f"从者_{svt_info.get('svtId')}" if svt_info else None
        
    result["assistMode"] = "从者礼装"
    result["assistIdx"] = 2
    
    # 结合回合战斗逻辑操作序列
    bbc_actions = convert_actions_to_bbc_rounds(actions, delegate)
    result.update(bbc_actions)
    
    return result

def parse_import_source(source: str):
    """
    智能解析用户的输入。
    返回 tuple: (quest_id, team_id, direct_data)
      - direct_data 如果有值，直接走本地免网络解析。
    """
    source = source.strip()
    
    # 纯数字判断
    if source.isdigit():
        num = int(source)
        if len(source) <= 6:
            return None, num, None # team_id
        else:
            return num, None, None # quest_id

    # 包含长串压缩数据 data=GH4...
    match_data = re.search(r'data=([A-Za-z0-9\-\_]+)', source)
    if match_data:
        return None, None, match_data.group(1)
        
    # 包含短链接 ID id=...
    match_id = re.search(r'id=(\d+)', source)
    if match_id:
        return None, int(match_id.group(1)), None

    return None, None, None

def fetch_and_convert(source: str, output_dir: Optional[str] = None) -> Optional[str]:
    """主入口编排：通过 source 获取数据并生成 BBC 字典配置"""
    quest_id, team_id, direct_data = parse_import_source(source)
    share_data = None
    
    if direct_data:
        logger.info("[Chaldea] 匹配到长链接数据特征，开启离线解码...")
        share_data = decode_content(direct_data)
        team_id = "offline"
        quest_id = (share_data.get("quest") or {}).get("id", "0") if share_data else "0"
    elif team_id:
        team_resp = fetch_team_by_id(team_id)
        if team_resp and "content" in team_resp:
            share_data = decode_content(team_resp["content"])
            quest_id = team_resp.get("questId", "0")
        else:
            logger.error("[Chaldea] 队伍接口无匹配数据。")
            return None
    elif quest_id:
        teams = fetch_teams_by_quest(quest_id, 3, 10)
        best = select_best_team(teams)
        if best and "content" in best:
            share_data = decode_content(best["content"])
            team_id = best.get("id", "top")
        else:
            logger.error("[Chaldea] 该关卡无可用队伍数据。")
            return None
            
    if not share_data:
        logger.error("[Chaldea] 数据结构提取失败。")
        return None

    bbc_config = chaldea_to_bbc(share_data)
    
    filename = f"chaldea_{quest_id}_{team_id}.json"
    filepath = os.path.join(output_dir or ".", filename)

    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(bbc_config, f, ensure_ascii=False, indent=4)
        
    logger.info(f"[Chaldea] 已保存队伍 JSON 到 {filepath}")
    return filename

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=str, required=True)
    parser.add_argument("--outd", type=str, default=".")
    args = parser.parse_args()
    fetch_and_convert(args.source, args.outd)
