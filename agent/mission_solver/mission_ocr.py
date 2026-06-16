"""
周常任务求解器 — OCR 读取游戏内任务进度

通过 MaaFramework 控制器截图，使用 OCR 识别任务描述和进度。
将 OCR 结果与 API 任务列表模糊匹配，设置 current_progress。

流程:
    1. 导航至游戏内"任务一览"界面
    2. 逐页截图 + OCR
    3. 裁剪描述区域 → OCR → 中文文字
    4. 裁剪进度区域 → OCR → "X/Y" 数字
    5. 描述文字与 API 列表模糊匹配 → 找到对应 Mission
    6. Y 值校验 + 设置 current_progress
    7. 滚动翻页 → 重复 2-6
    8. 返回主界面
"""

import logging
import re
from typing import Optional

from .models import Mission

logger = logging.getLogger("MissionOCR")

# FGO 游戏分辨率基准 (1280x720)
# 任务一览界面的区域定义
BASE_WIDTH = 1280
BASE_HEIGHT = 720

# 每个任务条目在列表中的区域 (基于 1280x720)
# 任务条目垂直堆叠：描述在上，进度在下
MISSION_ITEM_HEIGHT = 155     # 单条任务条目高度（实际截图测量值）
MISSION_LIST_TOP = 175        # 第1条任务顶部 y 坐标（"周常"标签下方）
MISSION_LIST_BOTTOM = 665     # 可见区域底部（滚动条上方）
MISSION_DESC_LEFT = 465       # 描述文字左边界（任务卡片内）
MISSION_DESC_RIGHT = 830      # 描述文字右边界（避开右侧报酬区域）
MISSION_PROGRESS_LEFT = 465   # "0/X"进度数字左边界
MISSION_PROGRESS_RIGHT = 540  # 进度数字右边界（只取数字区域）

# 可见任务条目数（一屏约 3 条）
VISIBLE_ITEMS = (MISSION_LIST_BOTTOM - MISSION_LIST_TOP) // MISSION_ITEM_HEIGHT

# 汇总任务关键词（需要跳过，不是周常任务本身）
SUMMARY_KEYWORDS = {"完成本周所有的御主任务", "完成本周所有御主任务", "完成所有的御主任务"}

# 中文停用词 (在模糊匹配中去除)
_STOP_WORDS = set("的了是在我你他她它们这那有和与或但不也从都把被让给向对".split())


def _extract_keywords(text: str) -> set[str]:
    """
    从中文文本中提取关键词集合。
    去除停用词，保留有意义的词片段。
    """
    # 去除换行和多余空格
    text = re.sub(r"[\n\r]+", " ", text).strip()
    # 去除【】等标记符号的内容（如【新手任务】）
    text = re.sub(r"【[^】]*】", "", text)
    # 分词：简单的 2-gram + 完整词
    words = set()
    # 按标点和空格分割
    segments = re.split(r"[，。、：；！？\s,.;!?]+", text)
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # 去除停用词字符
        seg = "".join(c for c in seg if c not in _STOP_WORDS)
        if len(seg) >= 2:
            words.add(seg)
        # 2-gram
        for i in range(len(seg) - 1):
            words.add(seg[i:i+2])
    return words


def _compute_similarity(text_a: str, text_b: str) -> float:
    """
    计算两段文本的相似度 (Jaccard 系数)。

    使用 2-gram 集合的交集/并集比。
    """
    kw_a = _extract_keywords(text_a)
    kw_b = _extract_keywords(text_b)
    if not kw_a or not kw_b:
        return 0.0
    intersection = kw_a & kw_b
    union = kw_a | kw_b
    return len(intersection) / len(union)


def match_mission_by_description(
    ocr_text: str,
    missions: list[Mission],
    threshold: float = 0.3,
) -> Optional[Mission]:
    """
    将 OCR 识别的文本与任务列表模糊匹配。

    Args:
        ocr_text: OCR 识别的任务描述文本
        missions: API 获取的任务列表
        threshold: 最低相似度阈值

    Returns:
        匹配到的 Mission，未匹配返回 None
    """
    if not ocr_text.strip():
        return None

    best_match = None
    best_score = 0.0

    for mission in missions:
        if mission.is_completed:
            continue  # 跳过已完成的任务

        score = _compute_similarity(ocr_text, mission.description)
        if score > best_score:
            best_score = score
            best_match = mission

    if best_score >= threshold and best_match is not None:
        logger.debug(f"匹配成功: '{ocr_text[:30]}...' → '{best_match.description[:30]}...' (score={best_score:.2f})")
        return best_match
    else:
        logger.debug(f"匹配失败: '{ocr_text[:30]}...' (best_score={best_score:.2f}, threshold={threshold})")
        return None


def parse_progress_text(text: str) -> Optional[tuple[int, int]]:
    """
    解析进度文本 "X/Y" 或 "X / Y"。

    Args:
        text: OCR 识别的进度文本

    Returns:
        (current, total) 元组，解析失败返回 None
    """
    if not text:
        return None

    # 尝试匹配 "X/Y" 格式
    match = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if match:
        current = int(match.group(1))
        total = int(match.group(2))
        return (current, total)

    # 尝试匹配 "X Y" 格式（OCR 可能丢失 /）
    match = re.search(r"(\d+)\s+(\d+)", text)
    if match:
        current = int(match.group(1))
        total = int(match.group(2))
        if total > 0 and current <= total:
            return (current, total)

    # 尝试匹配单个数字（可能是已完成数量）
    match = re.search(r"(\d+)", text)
    if match:
        value = int(match.group(1))
        return (value, 0)  # total=0 表示未知

    return None


def update_mission_progress_from_ocr(
    missions: list[Mission],
    ocr_results: list[dict],
) -> int:
    """
    根据 OCR 结果更新任务进度。

    Args:
        missions: 任务列表（将被原地修改 current_progress）
        ocr_results: OCR 识别结果列表，每项包含:
            - "description": 任务描述文字
            - "progress": 进度文字 (如 "3/15")

    Returns:
        成功匹配并更新的任务数量
    """
    updated = 0

    for result in ocr_results:
        desc_text = result.get("description", "")
        progress_text = result.get("progress", "")

        # 匹配任务
        mission = match_mission_by_description(desc_text, missions)
        if mission is None:
            continue

        # 解析进度
        progress = parse_progress_text(progress_text)
        if progress is None:
            logger.warning(f"进度解析失败: '{progress_text}' (任务: '{mission.description[:30]}')")
            continue

        current, total = progress

        # 校验: total 应与 mission.count 一致（允许 OCR 误差）
        if total > 0 and abs(total - mission.count) > 2:
            logger.warning(
                f"进度 total 不匹配: OCR={total}, mission.count={mission.count} "
                f"(任务: '{mission.description[:30]}')"
            )

        # 更新进度
        mission.current_progress = current
        updated += 1
        logger.info(
            f"进度更新: '{mission.description[:30]}...' "
            f"{current}/{mission.count} (remaining={mission.remaining})"
        )

    return updated


def get_mission_item_regions(
    screen_width: int,
    screen_height: int,
) -> list[dict[str, tuple[int, int, int, int]]]:
    """
    根据屏幕分辨率计算每个任务条目的描述区域和进度区域。

    返回的坐标格式为 (x1, y1, x2, y2)。

    Args:
        screen_width: 截图宽度
        screen_height: 截图高度

    Returns:
        列表，每项包含 "description" 和 "progress" 两个 ROI
    """
    # 计算缩放比例
    scale_x = screen_width / BASE_WIDTH
    scale_y = screen_height / BASE_HEIGHT

    regions = []
    for i in range(VISIBLE_ITEMS):
        y_top = MISSION_LIST_TOP + i * MISSION_ITEM_HEIGHT
        y_bottom = y_top + MISSION_ITEM_HEIGHT - 5  # 留 5px 间隔

        if y_bottom > MISSION_LIST_BOTTOM:
            break

        # 描述区域：条目上半部分（45% 高度，捕捉描述文字）
        desc_y_bottom = int(y_top + MISSION_ITEM_HEIGHT * 0.45)
        desc_roi = (
            int(MISSION_DESC_LEFT * scale_x),
            int(y_top * scale_y),
            int(MISSION_DESC_RIGHT * scale_x),
            int(desc_y_bottom * scale_y),
        )

        # 进度区域：条目中下部（55%-95% 高度，捕捉"目标进行度 0/X"）
        progress_y_top = int(y_top + MISSION_ITEM_HEIGHT * 0.50)
        progress_y_bottom = int(y_top + MISSION_ITEM_HEIGHT * 0.95)
        progress_roi = (
            int(MISSION_PROGRESS_LEFT * scale_x),
            int(progress_y_top * scale_y),
            int(MISSION_PROGRESS_RIGHT * scale_x),
            int(progress_y_bottom * scale_y),
        )

        regions.append({
            "description": desc_roi,
            "progress": progress_roi,
        })

    return regions


def is_summary_mission(ocr_text: str) -> bool:
    """
    判断 OCR 文本是否为汇总任务（"完成本周所有的御主任务"）。

    汇总任务不是周常任务本身，只是完成全部周常后的额外奖励。

    Args:
        ocr_text: OCR 识别的任务描述文本

    Returns:
        True 表示是汇总任务，应跳过
    """
    if not ocr_text:
        return False
    # 清洗文本后匹配
    cleaned = re.sub(r"[\s「」『』\u3000]+", "", ocr_text)
    for keyword in SUMMARY_KEYWORDS:
        if keyword in cleaned or cleaned in keyword:
            return True
    return False


def crop_image(image, roi: tuple[int, int, int, int]):
    """
    裁剪图片 ROI 区域。

    Args:
        image: numpy array (H, W, C)
        roi: (x1, y1, x2, y2)

    Returns:
        裁剪后的 numpy array
    """
    x1, y1, x2, y2 = roi
    # 边界检查
    h, w = image.shape[:2]
    x1 = max(0, min(x1, w))
    y1 = max(0, min(y1, h))
    x2 = max(0, min(x2, w))
    y2 = max(0, min(y2, h))
    return image[y1:y2, x1:x2]
