# MaaFgo 自动战斗：架构与实现说明

> **版本**：v1.5（对应分支 `feat/auto-battle-planner`）
> **日期**：2026-08-03
> **性质**：描述 `agent/battle/` **当前已落地代码**的架构与实现现状。
> **与设计稿的区别**：`MaaFgo_自动战斗技术方案_整合版.md` 是**设计/计划**；本文是**代码现状参考**（含"已实现 / 桩 / TODO"标注）。
> **当前状态**：V1b/V2 已通过真机验证；V3 Vision 实验层默认关闭，已完成离线测试与架构瘦身。

---

## 1. 概述

在 MaaFgo（基于 MaaFramework，MFW）中新增一条可选战斗后端：**原生自动战斗**。当前工作树已完成 V1b 核心闭环与 V2 固定 TurnPlan，并接入默认关闭的 V3 Vision 实验层：

- 逐回合识别选卡界面；
- 动态选 3 张卡（**宝具卡优先 + 面卡按卡色打分补齐**）；
- 原子点击执行选卡（当前以 `time.sleep` 间隔代替后置确认，真机验证可用）；
- 识别胜利/失败/异常并 fail-closed 停止；
- 支持从者技能、御主技能、选敌、换人及按回合 `TurnPlan`；`owner_slot` 与 `LLMDecider` 尚未完成。

与现有 BBC 战斗后端**并列**，默认流程不变。

---

## 2. 分层架构与依赖方向

```
┌─────────────────────────────────────────────────────────┐
│ agent/custom/auto_battle_action.py                        │  MFW 入口
│   @AgentServer.custom_action("auto_battle")               │
└───────────────────────────┬───────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────┐
│ agent/battle/runtime/runtime.py  AutoBattleRuntime        │  编排层
│   回合状态机：观测→决策→校验→执行→确认，fail-closed        │
└───────┬──────────────────┬───────────────────┬────────────┘
        ▼                  ▼                   ▼
┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ perception/  │  │ core/            │  │ execution/       │
│ 截图→State   │  │ 契约+决策+校验    │  │ 原子点击+确认     │
│ (集成层)     │  │ (纯逻辑, 无依赖)  │  │ (集成层)         │
└──────────────┘  └──────────────────┘  └──────────────────┘
```

**依赖规则**：

| 层 | 目录 | 允许依赖 | 耦合 MFW？ | 可离线测？ |
|---|---|---|---|---|
| 契约/决策/校验 | `battle/core/` | 仅 stdlib | 否 | 是（纯逻辑） |
| 感知 | `battle/perception/` | core + 传入的 `context` | 是（`run_recognition`） | 需静态截图 |
| 执行 | `battle/execution/` | 传入的 `context` | 是（`controller`） | 需 Fake/设备 |
| 编排 | attle/runtime/ | core + perception + execution + 可选 vision | 间接 | 需 Fake/设备 |
| Vision 实验层 | attle/vision/ | core + cv2 + 可选远程 Provider | 否（通过 Runtime 接入） | 是（Fake Provider） |
| 入口 | `custom/auto_battle_action.py` | 全部 + maa | 是 | 集成 |

`battle/core/` 严禁 import `maa` / `cv2` / `socket`，保证纯逻辑可在无设备环境单测。

---

## 3. 目录与文件现状

```
agent/battle/
├── core/                        ✅ 已实现（纯逻辑）
│   ├── enums.py                 Scene / CardColor / PrimitiveKind
│   ├── models.py                Confidence / CommandCard / NpCard / EnemyState /
│   │                            BattleState / CardPick / BattleAction / PrimitiveAction
│   ├── policy.py                Goal / CardPolicy / StrategyProfile
│   ├── decider.py               Decider 协议 + RuleDecider（V1b 打分选卡）
│   └── validator.py             validate() + Verdict（独立合法性/硬禁区校验）
├── perception/                  ✅ 已实现并验证（OCR 卡色/NP/场景识别均正常）
│   ├── config.py                节点名模板 + 阈值
│   └── perception.py            build(context, img) -> BattleState
├── execution/                   ✅ 已实现并验证（坐标正确，点击生效）
│   ├── coords.py                1280×720 坐标表（已填入实际值并验证）
│   └── executor.py              Executor：select_enemy/card/np + open_command_cards
├── runtime/                     ✅ 已实现
│   └── runtime.py               AutoBattleRuntime + BattleResult + 可选 Vision 接线
└── vision/                      🟡 实验性、默认关闭（8 个核心文件）
    ├── models.py / parser.py    远程结构化契约与严格解析
    ├── provider.py              Fake/Replay/OpenAI-compatible Provider + 创建入口
    ├── trigger.py               触发规则 + Runtime 上下文追踪
    ├── orchestrator.py          限流/去重/字段补丁/冲突记录
    └── config.py / prompts.py   配置与 Prompt

agent/custom/auto_battle_action.py   ✅ 已实现；支持 profile/max_turns/plan/vision，save_evidence 仍为 TODO
agent/main.py                        ✅ 已追加 `import auto_battle_action` 注册
assets/resource/base/pipeline/
  自动战斗_感知.json                   ✅ 已创建并验证，含卡色/场景/NP/敌人识别节点
  原生自动战斗.json                   ✅ 已创建并进入 interface 任务列表
tests/battle/                         ✅ 68 项离线测试通过（core/参数/本地感知/Vision/统一安全门与 Validator）
```

图例：✅ 已实现并验证；🟡 已接线但仍处实验/待验收；⬜ 占位/未开始。

---

## 4. 一个回合的调用链

真实流程是**两屏**：主界面（有攻击钮）→ 点攻击 → 选卡界面 → 选 3 张 → **选完第 3 张自动发动** → 20~40s 攻击动画 → 回主界面/胜利。

`AutoBattleRuntime.run()` 主循环（`turns < max_turns`）：

```
state = observe()   # post_screencap -> perception.build
场景分支：
  VICTORY                 -> success
  DEFEAT                  -> fail(defeat)
  DIALOG                  -> fail(unexpected_dialog)
  MAIN_BATTLE             -> executor.open_command_cards()（点攻击开卡）；失败 fail(open_cards_failed)
  COMMAND_SELECTION       -> decide -> validate（拒绝 fail(action_rejected:*)）
                             -> _execute_selection：逐 pick 选卡（前后 ROI 差确认）
                                任一失败 fail(selection_confirm_failed)
                             -> _wait_turn_settled()：等动画结束（轮询到主界面/胜利，超时 fail(stuck_after_attack)）
                             -> turns += 1
  UNKNOWN / ANIMATION      -> 有界等待到已知场景（超时 fail(stuck_unknown_scene)）
```

**关键**：攻击动画期间画面读成 UNKNOWN，属"宽容等待"（`_wait_turn_settled`，超时 60s），**不**当异常停止；只有决策/确认失败或真正卡死超时才停。选卡为自动发动，**没有独立的攻击确认点击**。

> **注意**：当前 `_execute_selection` 中每个 pick 之间用 `time.sleep(1)` 间隔，未做后置确认（`select_card`/`select_np` 始终返回 `True`）。`open_command_cards()` 也始终返回 `True`，仅点击攻击钮后 `time.sleep(2)` 等待开卡动画。真机验证表明在正常网络/模拟器条件下此方案可稳定运行；后续如需更高鲁棒性（网络抖动、模拟器卡顿等），仍建议补后置确认（见 §9）。

---

## 5. 领域契约（`core/models.py`）

不可变 `@dataclass(frozen=True)`，坐标绝不出现在契约里。

- `Confidence(value, source)`，`passes(threshold)`。
- `CommandCard(ui_slot 1..5, color, owner_slot|None, confidence)` —— 下排面卡；**V1 `owner_slot` 恒为 None**。
- `NpCard(servant_slot 1..3, confidence)` —— 上排宝具卡。
- `EnemyState(slot, alive, targeted, confidence)`。
- `BattleState(scene, scene_confidence, cards[5], np_cards[0..3], enemies, screenshot_id, schema_version, unknown_fields)`
  - `command_ready(0.95)`：场景为选卡且置信度达标。
  - `cards_ready(threshold)`：恰 5 张面卡且每张达标；阈值由 `StrategyProfile.min_card_confidence` 传入（当前默认 0.50）。
- `CardPick(kind ∈ {SELECT_CARD, SELECT_NP}, slot)` —— 一次选卡（面卡用 ui_slot，宝具卡用 servant_slot）。
- `BattleAction(target_enemy|None, picks[3], rationale_tag)` —— 技能/换人字段暂不建模（V2）。
- `PrimitiveAction(kind, slot)` —— 执行层受限原子动作，不含 x/y。

---

## 6. 决策器（`core/decider.py`）

`Decider` 是 Protocol；当前实现 `RuleDecider`，以后 `LLMDecider` 同签名替换即可。

`RuleDecider.decide(state)`：

1. **选目标**：优先当前已选中的存活敌人，否则第一个存活敌人。
2. **宝具卡优先**（`policy.np_first`）：把 `np_cards` 转成 `SELECT_NP` pick，最多取 3。
3. **面卡补齐**：`need = 3 - len(np_picks)`，从 5 张面卡里枚举 `permutations(cards, need)` 打分取最优：
   - 卡色优先级加权，靠前的卡权重更高（`weight * (n-pos)`）；
   - 同色链 +5；
   - 目标导向：`FINISH_WAVE→Buster / BUILD_NP→Arts / BUILD_STARS→Quick`，首卡 +3、末卡 +2。
4. 合并 `np_picks + face_picks`，截断到 3，返回 `BattleAction`。

组合规模 ≤ P(8,3)=336，全枚举无压力；owner 为 None 时不计 Brave/从者优先级。

---

## 7. 校验器（`core/validator.py`）

当前提供两个显式入口：`validate_main_action()` 校验主界面动作，`validate_card_action()` 校验选卡动作；旧 `validate()` 保留为选卡兼容别名。Runtime 在主界面校验前先调用 `skip_unusable_servant_skills()`：计划中的技能若明确 CD、状态未知、状态缺失或置信度不足，会记录具体字段并从本轮动作移除，随后继续开卡攻击；非法槽位、重复技能和非法目标不会被跳过，仍由 Validator 拒绝。选卡拒绝原因：

| reason | 条件 |
|---|---|
| `scene_not_command_selection` | 场景/置信度不达标 |
| `cards_not_confident` | 面卡未满 5 或置信度不足 |
| `need_exactly_3_picks` | picks 数 ≠ 3 |
| `duplicate_picks` | (kind, slot) 有重复 |
| `forbidden_pick_kind:*` | 出现非 SELECT_CARD/SELECT_NP 的动作（硬禁区兜底） |
| `card_not_present` | SELECT_CARD 槽位不在当前面卡 |
| `np_not_present` | SELECT_NP 槽位不在当前宝具卡 |
| `invalid_enemy_target` | 目标不在存活敌人集合 |

主界面校验现覆盖：场景与置信度、敌人存活/置信度、技能槽位、重复技能、技能可用态与置信度、目标范围、御主技能范围，以及换人必须伴随御主技能。正常 Runtime 会先跳过不可确认执行的从者技能，因此技能状态问题不会中止整场战斗；Validator 中的技能状态拒绝仍作为未经过安全门的动作兜底。技能效果语义、御主技能可用态和礼装身份仍需 TeamSnapshot/ServantKnowledge 后补。

---

## 8. 感知实现（`perception/perception.py`）

`build(context, img, screenshot_id) -> BattleState`：一次截图，在同一帧上跑多个识别节点拼状态。

- **场景**：依次跑 `config.SCENE_NODES` 中各节点（选卡→主界面→胜利→失败），命中即定场景，皆不中为 `UNKNOWN`。
- **卡色**：每张卡跑 `战斗_卡{i}` **OCR** 节点，识别卡面文字「力击/迅击/技击」映射为 B/A/Q；置信度取 OCR `best_result.score`；未命中或文字不匹配则置信度 0（标 `unknown_fields`）。
- **宝具卡**：`战斗_NP卡{1..3}` **OCR** 节点，识别 NP 数值文字，解析数字 ≥100 则视为该从者 NP 满（兼容 OCR 误识如 "1.0.0%"，用正则提取数字）。
- **敌人**：`战斗_敌人{slot}` 通过 OCR 判存活，`战斗_敌人{slot}_选中` 通过 ColorMatch 判当前目标；节点与点击坐标已标定并完成真机验证。

**与设计稿的差异**：设计稿描述卡色用 ColorMatch（HSV 像素计数 argmax），实际实现改用 **OCR 文字识别**（力击/迅击/技击 → B/A/Q）。NP 卡检测也从 TemplateMatch 改为 OCR 数值识别。这是实现时根据游戏 UI 实际情况做的调整——OCR 文字比 HSV 色彩更鲁棒。

**现状**：识别节点已在 `assets/resource/base/pipeline/自动战斗_感知.json` 中创建，ROI 已填入实际 1280×720 坐标值并**通过真机验证**。场景节点（选卡/主界面/胜利/失败）使用 TemplateMatch + OCR，模板图片已制作并验证。API 字段已按 MaaFw 5.12.2 核对（`RecognitionDetail.hit` / `best_result.score|text`）。

---

## 9. 执行实现（`execution/executor.py`）

`Executor(context, controller=None)`：`controller = controller or context.tasker.controller`。

- `open_command_cards()`：主界面点攻击钮（`coords.ATTACK_BTN`），**始终返回 `True`**（无后置确认）。
- `select_card(ui_slot)`：点击 `coords.CARD_ROI[ui_slot]` 的中心点，**始终返回 `True`**（无后置确认）。
- `select_np(servant_slot)`：点击 `coords.NP_CLICK[servant_slot]`，**始终返回 `True`**（无后置确认）。
- `select_enemy(slot)`：点击 `coords.ENEMY_POINT[slot]`，**已真机验证**（测试04 通过）。
- `cast_servant_skill(servant_slot, skill_index)`：点击从者技能按钮，**已真机验证**（测试01/02/06 通过）。
- `select_skill_target(target_ally)`：技能目标子屏选择队友，**已真机验证**（测试02 通过）。
- `cast_master_skill(skill_index)`：先点御主技能菜单再点技能，**已真机验证**（测试03 通过）。
- `order_change(starting_member_idx, sub_member_idx)`：换人界面选择首发/候补并确认，**已真机验证**（测试05 通过）。
- **无 `attack()`**：选完第 3 张自动发动。**硬禁区**：不提供 令咒/圣晶石/氪金/抽卡/补 AP 入口。

**与设计稿的差异**：设计稿描述用 numpy 像素差做后置确认（`_DIFF_THRESHOLD`），实际实现未做后置确认——所有原子操作点击后直接返回 `True`，以 `time.sleep` 间隔代替。真机验证表明此方案在正常条件下可稳定运行。后续如需更高鲁棒性可补后置确认。

**现状**：`coords.py` 已填入实际 1280×720 坐标值（面卡 ROI、NP ROI/点击点、攻击钮、从者技能、御主技能、换人界面）并**全部通过真机验证**。执行层不依赖 numpy（当前无像素差逻辑）。

---

## 10. 回合状态机（`runtime/runtime.py`）

`AutoBattleRuntime(context, controller, decider, profile)`，`run() -> BattleResult`。

> `controller` 由调用方（`auto_battle_action.py`）显式传入，因 Agent 模式下每次 `context.tasker.controller` 会获取新 handle，只有第一次有效。

- VICTORY→success；DEFEAT→fail(`defeat`)；DIALOG→fail(`unexpected_dialog`)。
- MAIN_BATTLE→`_execute_skills()`（技能/御主技能/换人，失败 fail(`skill_execution_failed`)）→`open_command_cards()`（失败 fail(`open_cards_failed`)）。
- COMMAND_SELECTION→decide→validate（拒绝 fail(`action_rejected:*`)）→`_execute_selection`（确认失败 fail(`selection_confirm_failed`)）→`_wait_turn_settled()` 等动画（超时 fail(`stuck_after_attack`)）→`turns++`。
- UNKNOWN/ANIMATION→有界等待到已知场景（超时 fail(`stuck_unknown_scene`)）。
- 超过 `profile.max_turns`→fail(`max_turns_exceeded`)。

等待常量：`_ANIMATION_TIMEOUT_S=60`（等 20~40s 攻击动画）、`_UNKNOWN_TIMEOUT_S=15`、`_POLL_FREEZE_MS=500`。`BattleResult(ok, reason, turns)` 含 `success()/fail()`。

**日志**：全程使用 `mfaalog.info()` 输出详细运行日志（场景判断、决策结果、执行步骤、等待状态等），便于调试。

---

## 11. MFW 集成（`custom/auto_battle_action.py` + `main.py`）

- 注册：`@AgentServer.custom_action("auto_battle")`；`agent/main.py` 追加 `import auto_battle_action`。
- 因 `main.py` 只把 `agent/custom` 加入 `sys.path`，本文件在导入前把 `agent/` 目录加入 `sys.path`，使 `battle` 可作顶层包导入。
- 参数：读取 `argv.custom_action_param`（JSON）；已支持 `strategy_profile`、`max_turns`、`plan.turns` 和可选 `vision` 配置；`save_evidence` 尚未实现。
- `controller` 在 action 层获取一次并传入 `AutoBattleRuntime`（Agent 模式下每次 `context.tasker.controller` 获取新 handle）。
- 返回 `CustomAction.RunResult(success=result.ok)`。
- Pipeline 入口 `原生自动战斗.json` 已创建并进入 interface；识别子节点定义于 `自动战斗_感知.json`。

---

## 12. 当前状态与待办

**V1b 已验证** ✅：核心闭环（主界面→开卡→选3张→等动画→下一回合→胜利）已在真机上跑通。

**V2 已验证** ✅（2026-08-03）：8 个测试任务全部真机通过——
- 从者技能（有/无目标）、御主技能、选敌、换人、技能→宝具→选卡、两回合 TurnPlan、胜利结算。
- 技能/换人/选敌/结算全链路首次真机验证通过，时序参数在正常条件下稳定可用。

**已验证可用**：
- `core` 的契约、规则决策、TurnPlan，以及主界面/选卡两阶段 Validator 已接入；
- `perception` 感知层：OCR 卡色识别、NP 数值识别、场景判断、技能目标子屏、换人界面识别均正常工作；
- `execution` 执行层：从者技能/御主技能/选敌/换人/选卡/宝具坐标全部正确，点击生效；
- `runtime` 回合循环：两屏流程 + 长动画宽容等待 + 技能/换人/结算分支均正常运转。

**待补全（非阻塞，但提升鲁棒性/完整性）**：
- **后置确认**（`executor.py`）：当前以 `time.sleep` 代替，真机可用但缺乏点击失效检测；
- **参数与入口完善**：`strategy_profile` / `max_turns` / `plan` / `vision` 已接入；仍需 GUI 中的自由结构 TurnPlan 配置和 `save_evidence`；
- `save_evidence`（失败存证据）未实现；
- 敌人 OCR 与选中态 ColorMatch 节点已标定；仍需黄金截图集持续回归；
- 离线测试现为 68 项全通过；已覆盖本地技能未知字段和 Runtime 跳过后继续攻击，仍缺完整整局 Fake 端口状态机测试；
- Runtime 仍直接依赖 perception/executor，尚未抽成端口，整局状态机难以离线测试；
- 结算继续点 + `战斗_结算完成` 节点待标定（`SETTLEMENT_CALIBRATED = False`）。

---

## 12.1 V3 Vision 实验层（瘦身后）

> 状态：实验性多模态感知补充，不是 `LLMDecider`，默认关闭。

运行链路：`MFW BattleState → VisionTrigger → VisionOrchestrator → requested_fields 受限补丁 → BattleState`。Vision 不直接执行设备动作。

`agent/battle/vision/` 现收敛为 8 个核心文件：

- `models.py` / `parser.py`：远程 JSON 契约与严格解析；
- `provider.py`：Provider 协议、Fake/Replay/OpenAI-compatible 实现及创建入口；
- `trigger.py`：触发规则与 Runtime 上下文追踪；
- `orchestrator.py`：调用限流、去重、字段作用域、冲突记录和状态补丁；
- `config.py` / `prompts.py` / `__init__.py`：配置、提示词和公开接口。

本次瘦身移除了原先独立的 Factory、Service、Coordinator、Merger、StateAdapter、StatePatch、RuntimeTracker 文件，不再执行 `BattleState → VisualObservation → merge → BattleState` 的重复往返。外部模型输出仍使用 `VisualObservation`，内部只对明确请求的字段打补丁。

---
## 13. 如何扩展

- **接入 LLM**：第一版统一动作 Validator 已完成；下一步完成 TeamSnapshot/技能语义与本地合法候选，再新增 `LLMDecider`，模型只允许在本地候选中选择。
- **V2 主动技能/NP 顺序**：✅ 已实现并验证。`BattleAction`、Executor、感知链及第一版技能/换人 Validator 已接入。
- **标定 ROI/坐标**：5 面卡/3 宝具卡/攻击钮/从者技能/御主技能/换人界面坐标均已验证通过；后续需标定结算继续点 + 用黄金集回归。

---

## 14. 已知限制

- V2 技能与 TurnPlan 已实现；`owner_slot` 仍未由稳定本地感知识别，因此 Brave/按角色喂卡策略尚未启用。
- **无后置确认**：所有原子操作点击后直接返回 `True`，以 `time.sleep` 间隔代替；真机验证可用，但缺乏点击失效检测。
- **参数入口仍不完整**：底层 JSON 参数已接入，但 GUI 尚不能方便编辑自由结构 TurnPlan；`save_evidence` 未实现。
- 单服/单分辨率（1280×720）优先；多服/多 UI 版本未覆盖。
- 未接入伤害/NP 模拟，选卡为启发式规则而非估值。
- 高风险动作靠"执行层不提供入口"硬性排除，非靠配置开关。
- Vision 仍为同步远程调用，可能拉长 Runtime 等待时间；模型场景结果的升级权限尚需进一步收紧。
