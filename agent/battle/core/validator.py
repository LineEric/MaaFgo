"""独立校验器。任何来源（规则/以后 LLM/导入）的 BattleAction 都必须过这里。纯 stdlib。"""
from __future__ import annotations

from dataclasses import dataclass

from .enums import PrimitiveKind
from .models import BattleAction, BattleState
from .policy import StrategyProfile

# 选卡阶段只允许这两类 pick；其余（尤其高风险动作）一律拒绝
_ALLOWED_PICK_KINDS = {PrimitiveKind.SELECT_CARD, PrimitiveKind.SELECT_NP}


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str = ""


def validate(action: BattleAction, state: BattleState, profile: StrategyProfile) -> Verdict:
    if not state.command_ready(profile.min_scene_confidence):
        return Verdict(False, "scene_not_command_selection")
    if not state.cards_ready(profile.min_card_confidence):
        return Verdict(False, "cards_not_confident")

    if len(action.picks) != 3:
        return Verdict(False, "need_exactly_3_picks")
    if len({(p.kind, p.slot) for p in action.picks}) != 3:
        return Verdict(False, "duplicate_picks")

    face_slots = {c.ui_slot for c in state.cards}
    np_slots = {c.servant_slot for c in state.np_cards}
    for p in action.picks:
        if p.kind not in _ALLOWED_PICK_KINDS:
            return Verdict(False, f"forbidden_pick_kind:{p.kind.value}")
        if p.kind is PrimitiveKind.SELECT_CARD and p.slot not in face_slots:
            return Verdict(False, "card_not_present")
        if p.kind is PrimitiveKind.SELECT_NP and p.slot not in np_slots:
            return Verdict(False, "np_not_present")

    alive = {e.slot for e in state.enemies if e.alive}
    if action.target_enemy is not None and action.target_enemy not in alive:
        return Verdict(False, "invalid_enemy_target")

    return Verdict(True)
