"""多模态视觉层离线测试。"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agent")))

from battle.core.enums import CardColor, Scene
from battle.core.models import BattleState, CommandCard, Confidence, EnemyState
from battle.vision.config import VisionConfig
from battle.vision.models import VisionRequest, VisionResponse, VisualCard, VisualObservation
from battle.vision.orchestrator import VisionOrchestrator, apply_visual_patch, encode_png
from battle.vision.parser import VisionParseError, parse_observation
from battle.vision.prompts import SYSTEM_PROMPT, build_user_prompt
from battle.vision.provider import FakeVisionProvider, OpenAICompatibleVisionProvider
from battle.vision.trigger import SceneTriggerContext, VisionRuntimeTracker, VisionTrigger


COMMAND_VALID = '''```json
{"schema_version":1,"scene":{"value":"command_selection","confidence":0.98},"cards":[{"ui_slot":1,"color":"B","owner_slot":3,"is_np":false,"confidence":0.92}],"servants":[],"enemies":[{"slot":2,"alive":true,"targeted":true,"hp_ratio":0.8,"confidence":0.9}],"dialogs":[],"confidence":0.91,"unknown_fields":[]}
```'''
VISION_JSON = '{"schema_version":1,"scene":"command_selection","cards":[{"ui_slot":1,"color":"B","owner_slot":3,"is_np":false,"confidence":0.88}],"servants":[],"enemies":[],"dialogs":[],"confidence":0.9,"unknown_fields":[]}'


def _state(*, screenshot_id: str = "shot-1") -> BattleState:
    return BattleState(
        Scene.COMMAND_SELECTION,
        Confidence(0.95, "mfw"),
        (CommandCard(1, CardColor.BUSTER, None, Confidence(0.95, "mfw")),),
        (),
        (EnemyState(1, True, True, Confidence(0.95, "mfw")),),
        screenshot_id=screenshot_id,
        unknown_fields=("card[1].owner_slot",),
    )


def _owner_context() -> SceneTriggerContext:
    return SceneTriggerContext(
        Scene.COMMAND_SELECTION,
        unknown_fields=("card[1].owner_slot",),
    )


def test_parse_visual_observation_from_json_fence():
    obs = parse_observation(COMMAND_VALID, evidence_id="shot-1")
    assert obs.scene is Scene.COMMAND_SELECTION
    assert obs.cards[0].color is CardColor.BUSTER
    assert obs.cards[0].owner_slot == 3
    assert obs.enemies[0].targeted is True
    assert obs.evidence_id == "shot-1"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"scene":"not-a-scene"}',
        '{"cards":[{"ui_slot":6}]}',
        '{"servants":[{"slot":1,"skill_available":[true,false]}]}',
        '{"enemies":[{"slot":1,"hp_ratio":2}]}',
    ],
)
def test_invalid_visual_output_is_rejected(raw):
    with pytest.raises(VisionParseError):
        parse_observation(raw)


def test_fake_provider_returns_structured_observation():
    provider = FakeVisionProvider(COMMAND_VALID)
    response = provider.analyze(VisionRequest(b"image", evidence_id="shot-2"))
    assert provider.calls == 1
    assert response.error is None
    assert response.observation is not None
    assert response.observation.evidence_id == "shot-2"


def test_fake_provider_rejects_invalid_output():
    response = FakeVisionProvider("not json").analyze(VisionRequest(b"image"))
    assert response.observation is None
    assert response.error


class FakeHttpResponse:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


def test_remote_provider_builds_payload_and_parses_response():
    response_data = {
        "choices": [{"message": {"content": '{"schema_version":1,"scene":"main_battle","cards":[],"servants":[],"enemies":[],"dialogs":[],"confidence":0.9,"unknown_fields":[]}'}}],
        "usage": {"total_tokens": 12},
    }
    captured = {}

    def urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeHttpResponse(response_data)

    provider = OpenAICompatibleVisionProvider(
        "https://example.test/v1/chat/completions",
        "secret",
        "vision-test",
        urlopen=urlopen,
    )
    response = provider.analyze(VisionRequest(b"image-bytes", evidence_id="e1"))
    assert response.observation.scene is Scene.MAIN_BATTLE
    assert response.usage["total_tokens"] == 12
    assert captured["body"]["model"] == "vision-test"


def test_remote_provider_returns_error_for_malformed_response():
    provider = OpenAICompatibleVisionProvider(
        "https://example.test",
        "secret",
        "vision-test",
        urlopen=lambda request, timeout: FakeHttpResponse({"choices": []}),
    )
    assert provider.analyze(VisionRequest(b"image")).error


def test_prompt_contains_constraints_and_context():
    prompt = build_user_prompt(VisionRequest(b"img", requested_fields=("cards.owner_slot",), turn_index=2))
    assert "cards.owner_slot" in prompt
    assert "turn_index" in prompt
    assert "JSON" in SYSTEM_PROMPT


def test_config_defaults_to_disabled():
    assert VisionConfig.from_mapping(None).enabled is False


def test_unknown_transition_trigger_rules():
    trigger = VisionTrigger()
    assert not trigger.evaluate(SceneTriggerContext(Scene.UNKNOWN, elapsed_since_action_ms=500, consecutive_unknown=4, frame_stable=True)).should_call
    assert not trigger.evaluate(SceneTriggerContext(Scene.UNKNOWN, elapsed_since_action_ms=4000, consecutive_unknown=4, frame_stable=False)).should_call
    decision = trigger.evaluate(SceneTriggerContext(Scene.UNKNOWN, elapsed_since_action_ms=4000, consecutive_unknown=3, frame_stable=True))
    assert decision.should_call
    assert decision.requested_fields == ("scene", "dialogs")


def test_missing_owner_requests_only_owner_field():
    decision = VisionTrigger().evaluate(_owner_context())
    assert decision.should_call
    assert decision.requested_fields == ("cards.owner_slot",)


def test_tracker_counts_unknown_and_tracks_action_elapsed():
    now = [100.0]
    tracker = VisionRuntimeTracker(clock=lambda: now[0])
    tracker.observe(Scene.MAIN_BATTLE, b"main")
    tracker.mark_action("open_cards")
    now[0] = 102.5
    first = tracker.observe(Scene.UNKNOWN, b"same")
    second = tracker.observe(Scene.UNKNOWN, b"same")
    assert first.context.previous_scene is Scene.MAIN_BATTLE
    assert first.context.last_action == "open_cards"
    assert first.context.elapsed_since_action_ms == 2500
    assert second.context.frame_stable is True
    assert second.context.consecutive_unknown == 2


def test_tracker_initial_unknown_uses_scene_elapsed_time():
    now = [100.0]
    tracker = VisionRuntimeTracker(clock=lambda: now[0])
    tracker.observe(Scene.UNKNOWN, b"same")
    now[0] = 103.0
    observation = tracker.observe(Scene.UNKNOWN, b"same")
    assert observation.context.elapsed_since_action_ms == 3000


def test_apply_visual_patch_fills_only_requested_owner():
    visual = VisualObservation(
        scene=Scene.MAIN_BATTLE,
        cards=(VisualCard(1, CardColor.QUICK, 3, False, 0.9),),
        confidence=0.9,
    )
    result, conflicts = apply_visual_patch(
        _state(),
        visual,
        requested_fields=("cards.owner_slot",),
    )
    assert result.cards[0].owner_slot == 3
    assert result.cards[0].color is CardColor.BUSTER
    assert result.cards[0].confidence.source == "mfw"
    assert result.scene is Scene.COMMAND_SELECTION
    assert result.unknown_fields == ()
    assert conflicts == ()


def test_apply_visual_patch_records_requested_conflict():
    state = _state()
    state = BattleState(
        state.scene,
        state.scene_confidence,
        (CommandCard(1, CardColor.BUSTER, 1, Confidence(0.95, "mfw")),),
        state.np_cards,
        state.enemies,
    )
    visual = VisualObservation(cards=(VisualCard(1, CardColor.ARTS, 3, False, 0.9),))
    result, conflicts = apply_visual_patch(
        state,
        visual,
        requested_fields=("cards.color", "cards.owner_slot"),
    )
    assert result.cards[0].color is CardColor.ARTS
    assert result.cards[0].owner_slot == 3
    assert "card[1].color" in conflicts
    assert "card[1].owner_slot" in conflicts


def test_orchestrator_skips_when_disabled():
    provider = FakeVisionProvider(VISION_JSON)
    orchestrator = VisionOrchestrator(provider)
    result = orchestrator.analyze_state_if_needed(b"img", _state(), _owner_context())
    assert result.call.skipped
    assert result.call.reason == "disabled"
    assert provider.calls == 0


def test_orchestrator_applies_requested_field_and_forwards_action():
    class CapturingProvider(FakeVisionProvider):
        request = None

        def analyze(self, request):
            self.request = request
            return super().analyze(request)

    provider = CapturingProvider(VISION_JSON)
    orchestrator = VisionOrchestrator(provider, VisionConfig(enabled=True))
    context = SceneTriggerContext(
        Scene.COMMAND_SELECTION,
        last_action="open_command_cards",
        unknown_fields=("card[1].owner_slot",),
    )
    result = orchestrator.analyze_state_if_needed(b"img", _state(), context)
    assert result.call.reason == "called"
    assert result.effective_state.cards[0].owner_slot == 3
    assert provider.request.requested_fields == ("cards.owner_slot",)
    assert provider.request.recent_actions == ("open_command_cards",)


def test_orchestrator_deduplicates_and_limits_per_turn():
    provider = FakeVisionProvider(VISION_JSON)
    orchestrator = VisionOrchestrator(provider, VisionConfig(enabled=True, max_calls_per_turn=1))
    first = orchestrator.analyze_state_if_needed(b"a", _state(screenshot_id="same"), _owner_context(), turn_index=0)
    duplicate = orchestrator.analyze_state_if_needed(b"b", _state(screenshot_id="same"), _owner_context(), turn_index=0)
    limited = orchestrator.analyze_state_if_needed(b"c", _state(screenshot_id="other"), _owner_context(), turn_index=0)
    reset = orchestrator.analyze_state_if_needed(b"d", _state(screenshot_id="new-turn"), _owner_context(), turn_index=1)
    assert first.call.reason == "called"
    assert duplicate.call.reason == "duplicate_evidence"
    assert limited.call.reason == "max_calls_per_turn"
    assert reset.call.reason == "called"
    assert provider.calls == 2


def test_orchestrator_converts_provider_exception_to_error():
    class BrokenProvider:
        def analyze(self, request):
            raise RuntimeError("boom")

    orchestrator = VisionOrchestrator(BrokenProvider(), VisionConfig(enabled=True))
    result = orchestrator.analyze_state_if_needed(b"img", _state(), _owner_context())
    assert result.call.reason == "provider_exception"
    assert result.call.response.error == "boom"


def test_encode_png_produces_decodable_payload():
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    image = numpy.zeros((8, 8, 3), dtype=numpy.uint8)
    payload = encode_png(image)
    decoded = cv2.imdecode(numpy.frombuffer(payload, dtype=numpy.uint8), cv2.IMREAD_COLOR)
    assert payload.startswith(b"\x89PNG")
    assert decoded.shape == image.shape