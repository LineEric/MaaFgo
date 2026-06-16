"""
周常任务自动执行 Action

从求解器获取当周最优刷本方案，逐关自动导航 + BBC 战斗。

工作流程:
  1. 从节点数据获取参数（队伍配置、苹果类型等）
  2. 调用求解器计算最优方案
  3. 构建 quest_id → (chapter, map_key) 索引
  4. 逐关执行：
     a. 合并章节 override + 关卡 override + BBC 参数
     b. context.override_pipeline() 注入配置
     c. context.run_task("通用战斗调度") 执行一次完整状态机
  5. 全部完成或失败时返回结果

依赖:
  - agent/mission_solver/ 下的求解器模块
  - agent/mission_solver/war_id_to_chapter.json
  - agent/mission_solver/quest_id_to_zhcn.json
  - assets/options/chapter.json
  - assets/options/quests/{章节}.json
  - assets/resource/base/pipeline/日常战斗.json (通用战斗调度)
"""

import json
import os
import sys
from typing import Optional

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 确保 custom 目录在 sys.path 中
_custom_dir = os.path.dirname(os.path.abspath(__file__))
if _custom_dir not in sys.path:
    sys.path.insert(0, _custom_dir)

import mfaalog

# 添加 mission_solver 到 sys.path
_agent_dir = os.path.dirname(_custom_dir)
_mission_solver_dir = os.path.join(_agent_dir, "mission_solver")
if _mission_solver_dir not in sys.path:
    sys.path.insert(0, _mission_solver_dir)

# 项目根目录
_PROJECT_DIR = os.path.normpath(os.path.join(_agent_dir, ".."))

# OCR 进度缓存（由 OcrReadMissionProgress Action 写入，由 SolveAndRunWeeklyMissions 读取）
_ocr_missions_cache: Optional[list[dict]] = None


def _apply_ocr_progress(missions, ocr_results: list[dict]) -> int:
    """将 OCR 缓存应用到任务列表"""
    from mission_solver.mission_ocr import update_mission_progress_from_ocr
    return update_mission_progress_from_ocr(missions, ocr_results)


def _load_json(filepath: str) -> dict:
    if not os.path.exists(filepath):
        mfaalog.error(f"[周常任务] 文件不存在: {filepath}")
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_chapter_overrides() -> dict[str, dict]:
    """
    加载 assets/options/chapter.json 中的章节切换配置。

    Returns:
        {chapter_name: {节点名: {覆盖字段}}}
    """
    filepath = os.path.join(_PROJECT_DIR, "assets", "options", "chapter.json")
    data = _load_json(filepath)
    option_data = data.get("option", {})
    result = {}
    for chapter, config in option_data.items():
        cases = config.get("cases", [])
        for case in cases:
            name = case.get("name", "")
            override = case.get("pipeline_override", {})
            if name and override:
                result[name] = override
    return result


def _load_quest_overrides() -> dict[str, dict[str, dict]]:
    """
    加载所有 assets/options/quests/{章节}.json 中的关卡配置。

    Returns:
        {chapter_name: {zhcn_name: pipeline_override_dict}}
    """
    quests_dir = os.path.join(_PROJECT_DIR, "assets", "options", "quests")
    if not os.path.exists(quests_dir):
        return {}

    result = {}
    for filename in os.listdir(quests_dir):
        if not filename.endswith(".json"):
            continue
        chapter = filename.replace(".json", "")
        filepath = os.path.join(quests_dir, filename)
        data = _load_json(filepath)
        option_data = data.get("option", {})
        for chapter_key, chapter_config in option_data.items():
            cases = chapter_config.get("cases", [])
            chapter_map = {}
            for case in cases:
                case_name = case.get("name", "")
                override = case.get("pipeline_override", {})
                if case_name and override:
                    chapter_map[case_name] = override
            if chapter_map:
                result[chapter] = chapter_map
    return result


def _build_quest_id_index() -> dict[int, tuple[str, str]]:
    """
    构建 quest_id → (chapter_cn, map_quest_key) 索引。

    使用本地缓存的 quest_id_to_zhcn.json 和 quest_enemies_CN.json。
    """
    # 加载 war_id → chapter 映射
    war_to_chapter = _load_json(
        os.path.join(_mission_solver_dir, "war_id_to_chapter.json")
    )
    # 将 null 值转为 None
    war_to_chapter = {int(k): v for k, v in war_to_chapter.items()}

    # 加载 quest_id → 中文名
    id_to_zhcn = _load_json(
        os.path.join(_mission_solver_dir, "quest_id_to_zhcn.json")
    )

    # 加载 quest_enemies (含 warId)
    quest_enemies = _load_json(
        os.path.join(_mission_solver_dir, "quest_enemies_CN.json")
    )

    # 加载 quest overrides
    quest_overrides = _load_quest_overrides()

    index = {}
    missing = []

    for quest_id_str, quest_data in quest_enemies.items():
        quest_id = int(quest_id_str)
        war_id = quest_data.get("warId", 0)

        # 跳过迦勒底之门
        chapter = war_to_chapter.get(war_id)
        if chapter is None:
            continue

        # 获取中文名
        zhcn_entry = id_to_zhcn.get(quest_id_str)
        if not zhcn_entry:
            missing.append((quest_id, "无中文名"))
            continue
        zhcn_name = zhcn_entry.get("name", "")

        # 在 quest_overrides 中查找
        chapter_overrides = quest_overrides.get(chapter, {})
        quest_override = chapter_overrides.get(zhcn_name)
        if not quest_override:
            missing.append((quest_id, f"无关卡配置: {zhcn_name}"))
            continue

        # 从 override 中提取 map_quest_key
        nav_override = quest_override.get("地图坐标导航", {})
        attach = nav_override.get("attach", {})
        map_quest_key = attach.get("quests", "")
        if not map_quest_key:
            missing.append((quest_id, f"无坐标 key: {zhcn_name}"))
            continue

        index[quest_id] = (chapter, map_quest_key)

    if missing:
        mfaalog.warning(f"[周常任务] 索引构建: {len(index)} 命中, {len(missing)} 缺失")
        for qid, reason in missing[:10]:
            mfaalog.warning(f"  quest {qid}: {reason}")

    return index


@AgentServer.custom_action("solve_and_run_weekly_missions")
class SolveAndRunWeeklyMissions(CustomAction):
    """周常任务自动执行 — 求解 + 逐关导航 + BBC 战斗"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        mfaalog.info("=" * 50)
        mfaalog.info("[周常任务] 周常任务自动执行 Action 启动")

        try:
            # 1. 获取参数
            node_data = context.get_node_data("周常任务自动执行")
            if not node_data:
                mfaalog.error("[周常任务] 无法获取节点数据")
                return CustomAction.RunResult(success=False)

            attach = node_data.get("attach", {})
            region = attach.get("region", "CN")
            progress = int(attach.get("progress", 0))
            bbc_team_config = attach.get("bbc_team_config", "")
            apple_type = attach.get("apple_type", "")
            battle_type = attach.get("battle_type", 0)
            support_order_mismatch = attach.get("support_order_mismatch", False)
            team_config_error = attach.get("team_config_error", False)
            ocr_enabled = str(attach.get("ocr_enabled", "false")).lower() == "true"

            mfaalog.info(f"[周常任务] 参数: region={region}, progress={progress}, team={bbc_team_config}, ocr={ocr_enabled}")

            # 2. 可选: 先导航到任务界面进行 OCR
            if ocr_enabled:
                mfaalog.info("[周常任务] OCR 模式: 导航至任务一览读取进度...")
                try:
                    context.run_task("导航至任务一览")
                except Exception as e:
                    mfaalog.warning(f"[周常任务] OCR 导航失败，将使用默认进度: {e}")

            # 3. 求解
            mfaalog.info("[周常任务] 正在求解周常任务...")
            plan = self._solve_missions(region, progress, ocr_enabled=ocr_enabled)
            if not plan:
                mfaalog.error("[周常任务] 求解失败或无可行方案")
                return CustomAction.RunResult(success=False)

            mfaalog.info(f"[周常任务] 求解完成: {len(plan)} 个关卡")

            # 3. 构建索引
            mfaalog.info("[周常任务] 构建关卡索引...")
            id_to_loc = _build_quest_id_index()
            mfaalog.info(f"[周常任务] 索引: {len(id_to_loc)} 个关卡可导航")

            # 4. 加载章节 override
            chapter_overrides = _load_chapter_overrides()
            quest_overrides = _load_quest_overrides()

            # 5. 逐关执行
            quest_map = self._load_quest_map(region)
            total = len(plan)
            completed = 0
            skipped = 0

            for i, (quest_id, count) in enumerate(sorted(plan.items(), key=lambda x: -x[1]), 1):
                quest_name = quest_map.get(quest_id, str(quest_id))
                loc = id_to_loc.get(quest_id)

                if not loc:
                    mfaalog.warning(f"[周常任务] [{i}/{total}] 跳过 {quest_name} (quest_id={quest_id}): 无导航映射")
                    skipped += 1
                    continue

                chapter, map_key = loc
                mfaalog.info(f"[周常任务] [{i}/{total}] 执行: {quest_name} ×{count}")
                mfaalog.info(f"  → 章节: {chapter}, 坐标: {map_key}")

                # 构建 pipeline_override
                chapter_override = chapter_overrides.get(chapter, {})
                quest_override = {}
                chapter_quests = quest_overrides.get(chapter, {})
                # 通过 map_key 反查 quest_override
                for cname, coverride in chapter_quests.items():
                    nav = coverride.get("地图坐标导航", {})
                    at = nav.get("attach", {})
                    if at.get("quests") == map_key:
                        quest_override = coverride
                        break

                # 合并三层 override
                full_override = {
                    **chapter_override,
                    **quest_override,
                    "执行BBC任务": {
                        "attach": {
                            "bbc_team_config": bbc_team_config,
                            "run_count": count,
                            "apple_type": apple_type,
                            "battle_type": battle_type,
                            "support_order_mismatch": support_order_mismatch,
                            "team_config_error": team_config_error,
                        }
                    }
                }

                # 注入 override
                context.override_pipeline(full_override)

                # 执行一次完整状态机
                mfaalog.info(f"[周常任务]  启动通用战斗调度...")
                try:
                    task_result = context.run_task("通用战斗调度")
                    if task_result and task_result.status.succeeded:
                        mfaalog.info(f"[周常任务]  ✅ {quest_name} 完成")
                        completed += 1
                    else:
                        error_msg = f"{quest_name} 执行失败"
                        mfaalog.error(f"[周常任务]  ❌ {error_msg}")
                        context.override_pipeline({
                            "bbc弹窗信息输出": {
                                "focus": {
                                    "Node.Recognition.Starting": f"<span style=\"color: #FF0000;\">周常任务失败: {error_msg}</span>"
                                }
                            }
                        })
                        return CustomAction.RunResult(success=False)
                except Exception as e:
                    mfaalog.error(f"[周常任务]  ❌ {quest_name} 异常: {e}")
                    return CustomAction.RunResult(success=False)

            # 6. 完成
            summary = f"周常任务完成: {completed} 关成功, {skipped} 关跳过"
            mfaalog.info(f"[周常任务] {summary}")
            context.override_pipeline({
                "bbc弹窗信息输出": {
                    "focus": {
                        "Node.Recognition.Starting": f"<span style=\"color: #00FF00;\">{summary}</span>"
                    }
                }
            })
            return CustomAction.RunResult(success=True)

        except Exception as e:
            mfaalog.error(f"[周常任务] 未预期异常: {e}")
            return CustomAction.RunResult(success=False)

    def _solve_missions(self, region: str, progress: int, ocr_enabled: bool = False) -> dict[int, int]:
        """调用求解器计算最优方案，可选 OCR 读取进度"""
        from mission_solver.data_loader import get_current_missions_with_fallback, get_free_quests
        from mission_solver.solver import solve

        # 优先在线获取任务数据（含 description）
        missions = get_current_missions_with_fallback(region)
        if not missions:
            mfaalog.error(f"[周常任务] 未找到当前周常任务 (region={region})")
            return {}

        mfaalog.info(f"[周常任务] 获取到 {len(missions)} 条任务")
        for i, m in enumerate(missions, 1):
            mfaalog.info(f"  {i}. {m.description[:40]}... (×{m.count})")

        # OCR 读取进度
        if ocr_enabled and _ocr_missions_cache is not None:
            mfaalog.info(f"[周常任务] 应用 OCR 进度数据...")
            _apply_ocr_progress(missions, _ocr_missions_cache)
            completed = sum(1 for m in missions if m.is_completed)
            mfaalog.info(f"[周常任务] OCR 进度: {completed}/{len(missions)} 已完成")

        quests = get_free_quests(region, progress)
        if not quests:
            mfaalog.error(f"[周常任务] 未找到候选副本 (region={region})")
            return {}

        result = solve(quests, missions)
        return result.plan

    def _load_quest_map(self, region: str) -> dict[int, str]:
        """加载 quest_id → 日文名 映射（用于日志输出）"""
        filepath = os.path.join(_mission_solver_dir, f"quest_enemies_{region}.json")
        data = _load_json(filepath)
        return {int(k): v.get("name", str(k)) for k, v in data.items()}


@AgentServer.custom_action("ocr_read_mission_progress")
class OcrReadMissionProgress(CustomAction):
    """
    OCR 读取游戏内任务进度。

    由 Pipeline 节点 "OCR读取任务进度" 触发。
    截图 → 裁剪 → OCR → 模糊匹配 → 写入缓存。
    支持多页滚动（一屏约 3 条，共 7 条需滚动 2 次）。
    """

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        global _ocr_missions_cache
        mfaalog.info("[周常任务-OCR] 开始读取任务进度...")

        try:
            import cv2
            import numpy as np
            import time
            from mission_solver.mission_ocr import (
                get_mission_item_regions,
                crop_image,
                is_summary_mission,
            )

            controller = context.tasker.controller

            # --- 确保处于"进行中"筛选状态 ---
            mfaalog.info("[周常任务-OCR] 检查筛选状态...")
            # 1280x720 下筛选按钮文字 ROI: [840,190,140,40]
            filter_roi_720 = (840, 190, 980, 230)
            for attempt in range(5):
                screenshot = controller.post_screenshot().wait().get()
                image = np.array(screenshot)
                if image.ndim == 3 and image.shape[2] == 4:
                    image = cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)

                h, w = image.shape[:2]
                scale_x = w / 1280
                roi = tuple(int(v * scale_x) for v in filter_roi_720)
                filter_img = crop_image(image, roi)
                text = self._ocr_image(context, filter_img)

                if "进行中" in text:
                    mfaalog.info(f"[周常任务-OCR] 当前筛选状态: 进行中 (attempt {attempt+1})")
                    break

                # 点击筛选按钮切换 (1280x720: 约 [950,210])
                click_x = int(950 * scale_x)
                click_y = int(210 * scale_x)
                mfaalog.info(f"[周常任务-OCR] 当前状态非'进行中'，点击筛选按钮切换... (attempt {attempt+1})")
                controller.post_click(click_x, click_y).wait()
                time.sleep(0.6)
            else:
                mfaalog.warning("[周常任务-OCR] 5次尝试仍未切换到'进行中'，将继续在当前状态下OCR")

            # --- 开始多页 OCR ---

            # 多页滚动识别
            _ocr_missions_cache = []
            max_scrolls = 3   # 最多滚动 3 次（7条任务，一屏3条）
            scroll_amount = 465  # 每次滑动约 465px（3条高度）

            for page in range(max_scrolls + 1):
                # 截图
                screenshot = controller.post_screenshot().wait().get()
                if screenshot is None:
                    mfaalog.error(f"[周常任务-OCR] 第 {page+1} 页截图失败")
                    break

                # 转换为 numpy array
                image = np.array(screenshot)
                if image.ndim == 3 and image.shape[2] == 4:
                    image = cv2.cvtColor(image, cv2.COLOR_RGBA2BGR)
                elif image.ndim == 2:
                    image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)

                h, w = image.shape[:2]
                mfaalog.info(f"[周常任务-OCR] 第 {page+1}/{max_scrolls+1} 页: {w}x{h}")

                # 获取 ROI 区域
                regions = get_mission_item_regions(w, h)
                mfaalog.info(f"[周常任务-OCR] 识别 {len(regions)} 个任务条目")

                page_results = 0
                for i, region in enumerate(regions):
                    desc_img = crop_image(image, region["description"])
                    progress_img = crop_image(image, region["progress"])

                    desc_text = self._ocr_image(context, desc_img)
                    progress_text = self._ocr_image(context, progress_img)

                    if desc_text:
                        # 跳过汇总任务
                        if is_summary_mission(desc_text):
                            mfaalog.info(f"  [{i}] 跳过汇总任务: '{desc_text[:30]}'")
                            continue

                        _ocr_missions_cache.append({
                            "description": desc_text,
                            "progress": progress_text or "",
                        })
                        page_results += 1
                        mfaalog.info(
                            f"  [{i}] 描述: '{desc_text[:30]}...' "
                            f"进度: '{progress_text}'"
                        )

                mfaalog.info(f"[周常任务-OCR] 第 {page+1} 页: {page_results} 条")

                # 滑动翻页（最后一页不需要滑）
                if page < max_scrolls:
                    mfaalog.info("[周常任务-OCR] 滑动翻页...")
                    controller.post_swipe(
                        int(w * 0.7), int(h * 0.6),
                        int(w * 0.7), int(h * 0.6) - scroll_amount,
                        500
                    ).wait()
                    import time
                    time.sleep(0.8)  # 等待滑动动画

            mfaalog.info(f"[周常任务-OCR] 识别完成: 共 {len(_ocr_missions_cache)} 条")
            return CustomAction.RunResult(success=True)

        except ImportError as e:
            mfaalog.error(f"[周常任务-OCR] 缺少依赖: {e} (需要 opencv-python)")
            return CustomAction.RunResult(success=False)
        except Exception as e:
            mfaalog.error(f"[周常任务-OCR] 异常: {e}")
            return CustomAction.RunResult(success=False)

    @staticmethod
    def _ocr_image(context: Context, image) -> str:
        """
        使用 MaaFramework 的 OCR 能力识别图片文字。
        备选：尝试使用 PaddleOCR 或 Tesseract。
        """
        try:
            import cv2
            _, buf = cv2.imencode(".png", image)
            img_bytes = buf.tobytes()

            # MaaFramework OCR
            result = context.run_recognition("OCR通用识别", image)
            if result and result.best_result:
                return result.best_result.text.strip()
        except Exception:
            pass
        return ""
