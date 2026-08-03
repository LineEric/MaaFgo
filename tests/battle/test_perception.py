"""本地战斗感知状态契约测试。"""
from __future__ import annotations

import os
import sys

AGENT = os.path.join(os.path.dirname(__file__), "..", "..", "agent")
sys.path.insert(0, os.path.abspath(AGENT))

from battle.core.enums import Scene
from battle.core.models import Confidence, ServantState, SkillState
from battle.perception import perception


def test_build_marks_unknown_servant_skill_fields(monkeypatch):
    servants = (
        ServantState(
            slot=1,
            skills=(
                SkillState(True, Confidence(0.99, "ocr:available")),
                SkillState(None, Confidence(0.0, "ocr:unknown")),
                SkillState(False, Confidence(0.99, "ocr:cd")),
            ),
            confidence=Confidence(1.0, "composite"),
        ),
    )
    monkeypatch.setattr(
        perception, "_detect_scene", lambda _context, _img: (Scene.MAIN_BATTLE, 0.99)
    )
    monkeypatch.setattr(perception, "_detect_servants", lambda _context, _img: servants)
    monkeypatch.setattr(perception, "_detect_enemies", lambda _context, _img: ())

    state = perception.build(None, None, screenshot_id="shot-1")

    assert state.unknown_fields == (
        "servant[1].skill[2].available",
    )