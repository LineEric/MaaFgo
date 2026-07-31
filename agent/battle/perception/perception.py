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
                           NpCard)
from . import config


def build(context, img, screenshot_id: str = "") -> BattleState:
    scene, sconf = _detect_scene(context, img)
    cards = tuple(_detect_card(context, img, i) for i in range(1, 6))
    np_cards = tuple(c for c in (_detect_np(context, img, s) for s in config.FRONTLINE_SLOTS) if c)
    enemies = _detect_enemies(context, img)

    unknown: List[str] = []
    for c in cards:
        if not c.confidence.passes(config.MIN_CARD_CONFIDENCE):
            unknown.append(f"card[{c.ui_slot}]")

    return BattleState(
        scene=scene,
        scene_confidence=Confidence(sconf, "scene"),
        cards=cards,
        np_cards=np_cards,
        enemies=enemies,
        screenshot_id=screenshot_id,
        unknown_fields=tuple(unknown),
    )


def _reco(context, node: str, img):
    """跑一个识别节点，返回 RecognitionDetail 或 None。"""
    return context.run_recognition(node, img)


def _detect_scene(context, img) -> Tuple[Scene, float]:
    # 命中哪个场景节点就是哪个；都不命中 -> UNKNOWN
    for scene_key, node in config.SCENE_NODES.items():
        r = _reco(context, node, img)
        if r and r.hit:
            score = getattr(getattr(r, "best_result", None), "score", 1.0) or 1.0
            return Scene(scene_key), float(score)
    return Scene.UNKNOWN, 0.0


def _detect_card(context, img, ui_slot: int) -> CommandCard:
    # 对该卡 ROI 跑 B/A/Q 三个 ColorMatch，比 count 取最大
    counts = {}
    for color in ("B", "A", "Q"):
        node = config.CARD_COLOR_NODE.format(ui_slot=ui_slot, color=color)
        r = _reco(context, node, img)
        counts[color] = _count(r)
    total = sum(counts.values())
    if total <= 0:
        # 全 0：识别失败，标未知（用 Buster 占位但置信度 0）
        return CommandCard(ui_slot, CardColor.BUSTER, None, Confidence(0.0, "colormatch"))
    best = max(counts, key=counts.get)
    conf = counts[best] / total
    return CommandCard(ui_slot, CardColor(best), owner_slot=None,
                       confidence=Confidence(conf, "colormatch"))


def _detect_np(context, img, servant_slot: int) -> Optional[NpCard]:
    node = config.NP_CARD_NODE.format(servant_slot=servant_slot)
    r = _reco(context, node, img)
    if r and r.hit:
        score = getattr(getattr(r, "best_result", None), "score", 1.0) or 1.0
        return NpCard(servant_slot, Confidence(float(score), "template"))
    return None


def _detect_enemies(context, img) -> Tuple[EnemyState, ...]:
    out: List[EnemyState] = []
    for slot in config.ENEMY_SLOTS:
        alive_r = _reco(context, config.ENEMY_NODE.format(slot=slot), img)
        alive = bool(alive_r and alive_r.hit)
        if not alive:
            continue
        tgt_r = _reco(context, config.ENEMY_TARGET_NODE.format(slot=slot), img)
        targeted = bool(tgt_r and tgt_r.hit)
        score = getattr(getattr(alive_r, "best_result", None), "score", 1.0) or 1.0
        out.append(EnemyState(slot, True, targeted, Confidence(float(score), "template")))
    return tuple(out)


def _count(reco) -> int:
    if not (reco and reco.hit):
        return 0
    best = getattr(reco, "best_result", None)
    return int(getattr(best, "count", 0) or 0)
