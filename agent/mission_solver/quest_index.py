"""
quest_id → (chapter_cn, map_quest_key) 索引构建器

将求解器的 quest_id 映射到 MaaFgo 大地图导航系统所需的 (章节中文名, 坐标 key)。

工作流程:
  1. 加载 war_id_to_chapter.json（war_id → 中文章节名）
  2. 加载 quest_id_to_zhcn.json（quest_id → 中文名，从 Atlas Academy API 获取）
  3. 加载 quest_enemies_CN.json（求解器数据，含 warId）
  4. 加载所有 assets/options/quests/{章节}.json（关卡配置，含坐标 key）
  5. 对每个 quest_id:
       chapter = war_id_to_chapter[quest.warId]
       zhcn_name = quest_id_to_zhcn[quest_id]
       map_quest_key = quests/{chapter}.json 中找 case.name == zhcn_name 的
                        pipeline_override.地图坐标导航.attach.quests
       → 索引为 (chapter, map_quest_key)
  6. 输出统计：命中数 / 缺失数
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("QuestIndex")

# 模块所在目录
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
# 项目根目录
_PROJECT_DIR = os.path.normpath(os.path.join(_MODULE_DIR, "..", ".."))


def _load_json(filepath: str) -> dict:
    if not os.path.exists(filepath):
        logger.warning(f"文件不存在: {filepath}")
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def load_war_id_to_chapter() -> dict[int, Optional[str]]:
    """
    加载 war_id → 中文章节名 映射。

    Returns:
        {war_id: chapter_name}，chapter_name 为 None 表示该 war 无大地图导航
    """
    filepath = os.path.join(_MODULE_DIR, "war_id_to_chapter.json")
    data = _load_json(filepath)
    result = {}
    for k, v in data.items():
        result[int(k)] = v  # v 可能是 null (None)
    return result


def load_quest_id_to_zhcn() -> dict[int, dict]:
    """
    加载 quest_id → 中文名 映射。

    Returns:
        {quest_id: {name, warId, spotName}}
    """
    filepath = os.path.join(_MODULE_DIR, "quest_id_to_zhcn.json")
    data = _load_json(filepath)
    result = {}
    for k, v in data.items():
        result[int(k)] = v
    return result


def load_quest_enemies() -> dict[int, dict]:
    """
    加载求解器副本数据。

    Returns:
        {quest_id: {id, name, warId, ...}}
    """
    filepath = os.path.join(_MODULE_DIR, "quest_enemies_CN.json")
    data = _load_json(filepath)
    result = {}
    for k, v in data.items():
        result[int(k)] = v
    return result


def load_quest_overrides() -> dict[str, dict[str, str]]:
    """
    加载所有 assets/options/quests/{章节}.json 中的关卡配置。

    Returns:
        {chapter_name: {zhcn_name: map_quest_key}}
        即：{ "冬木": { "宅邸残迹": "未确认坐标XA", ... }, ... }
    """
    quests_dir = os.path.join(_PROJECT_DIR, "assets", "options", "quests")
    if not os.path.exists(quests_dir):
        logger.warning(f"quests 目录不存在: {quests_dir}")
        return {}

    result = {}
    for filename in os.listdir(quests_dir):
        if not filename.endswith(".json"):
            continue
        chapter = filename.replace(".json", "")
        filepath = os.path.join(quests_dir, filename)
        data = _load_json(filepath)

        # 解析 option 结构
        option_data = data.get("option", {})
        for chapter_key, chapter_config in option_data.items():
            cases = chapter_config.get("cases", [])
            chapter_map = {}
            for case in cases:
                case_name = case.get("name", "")
                pipeline_override = case.get("pipeline_override", {})
                nav_override = pipeline_override.get("地图坐标导航", {})
                attach = nav_override.get("attach", {})
                quests_key = attach.get("quests", "")
                if case_name and quests_key:
                    chapter_map[case_name] = quests_key
            if chapter_map:
                result[chapter] = chapter_map

    return result


def build_quest_id_index() -> dict[int, tuple[str, str]]:
    """
    构建 quest_id → (chapter_cn, map_quest_key) 索引。

    Returns:
        {quest_id: (chapter_cn, map_quest_key)}
    """
    war_to_chapter = load_war_id_to_chapter()
    id_to_zhcn = load_quest_id_to_zhcn()
    quest_enemies = load_quest_enemies()
    quest_overrides = load_quest_overrides()

    logger.info(f"war_id_to_chapter: {len(war_to_chapter)} 条")
    logger.info(f"quest_id_to_zhcn: {len(id_to_zhcn)} 条")
    logger.info(f"quest_enemies: {len(quest_enemies)} 条")
    logger.info(f"quest_overrides: {sum(len(v) for v in quest_overrides.values())} 条 (覆盖 {len(quest_overrides)} 个章节)")

    index = {}
    missing_zhcn = []       # 缺中文名
    missing_chapter = []    # 缺章节映射
    missing_quest_file = [] # 缺关卡配置
    skipped_gate = []       # 迦勒底之门（跳过）

    for quest_id, quest_data in quest_enemies.items():
        war_id = quest_data.get("warId", 0)

        # 跳过迦勒底之门（修炼场，无大地图导航）
        chapter = war_to_chapter.get(war_id)
        if chapter is None:
            skipped_gate.append(quest_id)
            continue

        # 获取中文名
        zhcn_entry = id_to_zhcn.get(quest_id)
        if not zhcn_entry:
            missing_zhcn.append(quest_id)
            continue
        zhcn_name = zhcn_entry.get("name", "")

        # 在 quest_overrides 中查找
        chapter_overrides = quest_overrides.get(chapter, {})
        map_quest_key = chapter_overrides.get(zhcn_name)

        if not map_quest_key:
            missing_quest_file.append((quest_id, chapter, zhcn_name))
            continue

        index[quest_id] = (chapter, map_quest_key)

    # 输出统计
    logger.info(f"\n=== 索引构建统计 ===")
    logger.info(f"  命中: {len(index)} 个 quest")
    logger.info(f"  跳过(迦勒底之门): {len(skipped_gate)} 个")
    logger.info(f"  缺失(无中文名): {len(missing_zhcn)} 个")
    logger.info(f"  缺失(无章节映射): {len(missing_chapter)} 个")
    logger.info(f"  缺失(无关卡配置): {len(missing_quest_file)} 个")

    if missing_zhcn:
        logger.warning(f"  缺中文名的 quest_id (前10): {missing_zhcn[:10]}")
        logger.warning(f"  请运行: python tools/update_quest_data.py --zh-names")
    if missing_quest_file:
        logger.warning(f"  缺关卡配置 (前10): {missing_quest_file[:10]}")
        logger.warning(f"  这些关卡在 quest_enemies 中有但不在 quests/*.json 中")

    return index


def dry_run():
    """
    dry-run 模式：求解 + 映射，输出理论会执行的关卡序列。
    不连接 BBC，纯离线验证。
    """
    from .solver import solve
    from .data_loader import get_current_missions, get_free_quests

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=== 周常任务自动执行 — Dry Run ===\n")

    # 1. 获取当前周常任务
    missions = get_current_missions("CN")
    if not missions:
        print("未找到当前周的周常任务数据。")
        return

    print(f"当前周常任务 ({len(missions)} 条):")
    for i, m in enumerate(missions, 1):
        print(f"  {i}. {m.description} (×{m.count})")

    # 2. 获取候选副本
    quests = get_free_quests("CN")
    print(f"\n候选副本: {len(quests)} 个")

    # 3. 求解
    print("\n求解中...")
    result = solve(quests, missions)

    if not result.plan:
        print("\n无法找到可行方案。")
        return

    # 4. 构建索引
    print("\n构建 quest_id 索引...")
    index = build_quest_id_index()

    # 5. 输出映射结果
    print(f"\n=== 最优方案 (总 AP: {result.total_ap}, 总次数: {result.total_runs}) ===\n")

    quest_map = {q.id: q for q in quests}
    all_mapped = True

    for quest_id, count in sorted(result.plan.items(), key=lambda x: -x[1]):
        quest = quest_map.get(quest_id)
        name = quest.name if quest else str(quest_id)
        ap = quest.consume * count if quest else 0

        loc = index.get(quest_id)
        if loc:
            chapter, map_key = loc
            print(f"  ✅ {name}  ×{count}  (AP {ap})")
            print(f"     → 章节: {chapter}, 坐标: {map_key}")
        else:
            print(f"  ❌ {name}  ×{count}  (AP {ap})")
            print(f"     → 无映射，将被跳过")
            all_mapped = False

    if all_mapped:
        print(f"\n✅ 全部映射命中！可以开始实机运行。")
    else:
        print(f"\n⚠️  存在未映射的关卡，请先补充数据。")

    print()


if __name__ == "__main__":
    dry_run()
