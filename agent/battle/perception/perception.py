"""感知：一帧截图 -> BattleState。

集成层，可依赖 MFW（通过传入的 context），但不直接 import maa 类型——
只用 context.run_recognition(node_name, img) 和其返回的 RecognitionDetail。

各识别结果字段（已按 MaaFw 5.12.2 核对）：
  reco.hit: bool
  reco.best_result.count   ColorMatch / FeatureMatch
  reco.best_result.score   TemplateMatch / OCR
  reco.best_result.text    OCR
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..core.enums import CardColor, Scene
from ..core.models import (BattleState, CommandCard, Confidence, EnemyState,
                           NpCard, ServantState, SkillState)
from . import config


def build(context, img, screenshot_id: str = "") -> BattleState:
    scene, sconf = _detect_scene(context, img)

    # 只有选卡界面才需要解析卡牌；主界面/动画等无卡可读
    cards: Tuple[CommandCard, ...] = ()
    np_cards: Tuple[NpCard, ...] = ()
    servants: Tuple[ServantState, ...] = ()
    unknown: List[str] = []
    if scene is Scene.COMMAND_SELECTION:
        cards = tuple(_detect_card(context, img, i) for i in range(1, 6))
        np_cards = tuple(c for c in (_detect_np(context, img, s) for s in config.FRONTLINE_SLOTS) if c)
        for c in cards:
            if not c.confidence.passes(config.MIN_CARD_CONFIDENCE):
                unknown.append(f"card[{c.ui_slot}]")
    elif scene in (Scene.MAIN_BATTLE, Scene.SKILL_TARGET_SELECTION):
        servants = _detect_servants(context, img)
    elif scene is Scene.ORDER_CHANGE:
        # 换人界面：检测从者技能状态无意义，只识别场景即可
        pass

    enemies = _detect_enemies(context, img)

    return BattleState(
        scene=scene,
        scene_confidence=Confidence(sconf, "scene"),
        cards=cards,
        np_cards=np_cards,
        enemies=enemies,
        servants=servants,
        screenshot_id=screenshot_id,
        unknown_fields=tuple(unknown),
    )


def _reco(context, node: str, img):
    """跑一个识别节点，返回 RecognitionDetail 或 None。"""
    return context.run_recognition(node, img)


def reached_post_battle(context, img) -> bool:
    """结算点击流是否已走完（回到关卡列表/主界面）。依赖 SETTLEMENT_DONE_NODE（待标定）。"""
    r = _reco(context, config.SETTLEMENT_DONE_NODE, img)
    return bool(r and r.hit)


def _detect_scene(context, img) -> Tuple[Scene, float]:
    # 命中哪个场景节点就是哪个；都不命中 -> UNKNOWN
    for scene_key, node in config.SCENE_NODES.items():
        r = _reco(context, node, img)
        if r and r.hit:
            score = getattr(getattr(r, "best_result", None), "score", 1.0) or 1.0
            return Scene(scene_key), float(score)
    return Scene.UNKNOWN, 0.0


# OCR 文本 -> CardColor 映射
_CARD_TEXT_MAP = {
    "力击": CardColor.BUSTER,
    "技击": CardColor.ARTS,
    "迅击": CardColor.QUICK,
}


def _detect_card(context, img, ui_slot: int) -> CommandCard:
    # OCR 识别卡牌文字（力击/迅击/技击）
    node = config.CARD_NODE.format(ui_slot=ui_slot)
    r = _reco(context, node, img)
    if not r or not r.hit or not r.best_result:
        return CommandCard(ui_slot, CardColor.BUSTER, None, Confidence(0.0, "ocr"))
    text = getattr(r.best_result, "text", "") or ""
    score = getattr(r.best_result, "score", 1.0) or 1.0
    color = _CARD_TEXT_MAP.get(text.strip())
    if color is None:
        return CommandCard(ui_slot, CardColor.BUSTER, None, Confidence(0.0, "ocr"))
    return CommandCard(ui_slot, color, owner_slot=None, confidence=Confidence(float(score), "ocr"))


def _detect_np(context, img, servant_slot: int) -> Optional[NpCard]:
    import re
    node = config.NP_CARD_NODE.format(servant_slot=servant_slot)
    r = _reco(context, node, img)
    if r and r.hit:
        text = getattr(getattr(r, "best_result", None), "text", "")
        if text:
            # OCR 可能把 "100%" 误识为 "1.0.0%" 等，去掉非数字字符后拼接
            digits = re.sub(r'\D', '', text)
            if digits:
                val = int(digits)
                if val >= 100:
                    score = getattr(getattr(r, "best_result", None), "score", 1.0) or 1.0
                    return NpCard(servant_slot, Confidence(float(score), "ocr"))
    return None


def _detect_enemies(context, img) -> Tuple[EnemyState, ...]:
    out: List[EnemyState] = []
    for slot in config.ENEMY_SLOTS:
        alive_r = _reco(context, config.ENEMY_NODE.format(slot=slot), img)
        alive = bool(alive_r and alive_r.hit)
        if not alive:
            continue
        # 选中检测暂未实现，默认 False
        targeted = False
        score = getattr(getattr(alive_r, "best_result", None), "score", 1.0) or 1.0
        out.append(EnemyState(slot, True, targeted, Confidence(float(score), "ocr")))
    return tuple(out)


def _count(reco) -> int:
    if not (reco and reco.hit):
        return 0
    best = getattr(reco, "best_result", None)
    return int(getattr(best, "count", 0) or 0)


def _detect_servants(context, img) -> Tuple[ServantState, ...]:
    """检测前排从者的技能可用性。

    识别逻辑：OCR 读取技能 CD 节点，若命中"剩余"文字则技能在 CD 中不可用，
    否则视为可用。confidence 来自 OCR score。
    """
    out: List[ServantState] = []
    for slot in config.FRONTLINE_SLOTS:
        skills: List[SkillState] = []
        for idx in range(1, 4):
            cd_node = config.SERVANT_SKILL_CD_NODE.format(servant_slot=slot, skill_index=idx)
            r = _reco(context, cd_node, img)
            if r and r.hit:
                # OCR 命中"剩余" → 技能在 CD 中
                score = getattr(getattr(r, "best_result", None), "score", 1.0) or 1.0
                skills.append(SkillState(False, Confidence(float(score), "ocr:cd")))
            else:
                # 未命中"剩余" → 技能可用
                skills.append(SkillState(True, Confidence(0.5, "ocr:available")))
        out.append(ServantState(slot, tuple(skills), Confidence(1.0, "composite")))
    return tuple(out)
