"""
quest_id → 国服中文名 数据获取工具

从 Atlas Academy API 拉取国服 Free 副本的中文名，建立 quest_id → 中文名 映射。
保存到 agent/mission_solver/quest_id_to_zhcn.json，供索引构建器使用。

用法:
    python tools/update_quest_data.py --zh-names       # 更新中文名映射
    python tools/update_quest_data.py --zh-names --force  # 强制重新拉取全部
"""

import json
import logging
import os
import ssl
import sys
import time
import urllib.request
from typing import Optional

logger = logging.getLogger("QuestZhNames")

ATLAS_API = "https://api.atlasacademy.io"

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_TOOLS_DIR, "..", "agent", "mission_solver")


def _fetch_json(url: str, timeout: int = 30, retries: int = 3) -> Optional[dict]:
    """下载 JSON，带重试"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={"User-Agent": "MaaFgo/1.0"})

    for attempt in range(retries):
        try:
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.warning(f"  [404] {url}")
                return None
            if attempt < retries - 1:
                logger.warning(f"  重试 ({attempt + 1}/{retries}): {e}")
                time.sleep(2)
            else:
                logger.warning(f"  失败: {e}")
                return None
        except Exception as e:
            if attempt < retries - 1:
                logger.warning(f"  重试 ({attempt + 1}/{retries}): {e}")
                time.sleep(2)
            else:
                logger.warning(f"  失败: {e}")
                return None


def _get_quest_phase(quest_id: int) -> Optional[int]:
    """
    获取 quest 的有效 phase。
    大部分 free quest 的 phase 是 3，但有些可能不同。
    先尝试 phase=3，如果 404 则尝试 phase=1。
    """
    for phase in [3, 1, 2]:
        url = f"{ATLAS_API}/nice/CN/quest/{quest_id}/{phase}"
        data = _fetch_json(url)
        if data:
            return phase
    return None


def fetch_zh_names(quest_ids: list[int], force: bool = False) -> dict[str, dict]:
    """
    从 Atlas Academy API 拉取 quest 的中文名。

    Args:
        quest_ids: quest_id 列表
        force: 是否强制重新拉取（忽略已有缓存）

    Returns:
        {quest_id_str: {name, warId, spotName}} 字典
    """
    # 加载已有缓存
    output_path = os.path.join(DATA_DIR, "quest_id_to_zhcn.json")
    existing = {}
    if not force and os.path.exists(output_path):
        with open(output_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        logger.info(f"已有 {len(existing)} 条中文名缓存")

    result = dict(existing)
    new_count = 0
    skip_count = 0
    error_count = 0

    for i, qid in enumerate(quest_ids):
        qid_str = str(qid)

        # 如果已有缓存且不强制，跳过
        if qid_str in existing and not force:
            skip_count += 1
            continue

        # 获取有效 phase
        phase = _get_quest_phase(qid)
        if phase is None:
            logger.warning(f"  [{i+1}/{len(quest_ids)}] quest {qid}: 无法获取数据")
            error_count += 1
            continue

        url = f"{ATLAS_API}/nice/CN/quest/{qid}/{phase}"
        data = _fetch_json(url)
        if not data:
            error_count += 1
            continue

        result[qid_str] = {
            "name": data.get("name", ""),
            "warId": data.get("warId", 0),
            "spotName": data.get("spotName", ""),
        }
        new_count += 1

        if (i + 1) % 20 == 0:
            logger.info(f"  进度: {i+1}/{len(quest_ids)}, 新增 {new_count}, 跳过 {skip_count}, 失败 {error_count}")
            # 每 20 条保存一次，避免中途失败丢失进度
            _save_zh_names(result, output_path)

    _save_zh_names(result, output_path)
    logger.info(f"完成: 新增 {new_count}, 跳过 {skip_count}, 失败 {error_count}, 总计 {len(result)}")
    return result


def _save_zh_names(data: dict, output_path: str):
    """保存中文名映射到 JSON 文件"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"已保存 {len(data)} 条中文名到 {output_path}")


def load_zh_names() -> dict[str, dict]:
    """加载本地中文名映射"""
    output_path = os.path.join(DATA_DIR, "quest_id_to_zhcn.json")
    if not os.path.exists(output_path):
        return {}
    with open(output_path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_zh_name(quest_id: int) -> Optional[str]:
    """获取单个 quest 的中文名"""
    mapping = load_zh_names()
    entry = mapping.get(str(quest_id))
    if entry:
        return entry.get("name")
    return None
