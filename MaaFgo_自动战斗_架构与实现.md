# MaaFgo 自动战斗：架构与实现说明

> **版本**：v1.0（对应分支 `feat/auto-battle-planner`）
> **日期**：2026-07-31
> **性质**：描述 `agent/battle/` **当前已落地代码**的架构与实现现状。
> **与设计稿的区别**：`MaaFgo_自动战斗技术方案_整合版.md` 是**设计/计划**；本文是**代码现状参考**（含"已实现 / 桩 / TODO"标注）。

---

## 1. 概述

在 MaaFgo（基于 MaaFramework，MFW）中新增一条可选战斗后端：**原生自动战斗**。当前实现范围为 **V1b**：

- 逐回合识别选卡界面；
- 动态选 3 张卡（**宝具卡优先 + 面卡按卡色打分补齐**）；
- 每步原子点击后做后置确认；
- 识别胜利/失败/异常并 fail-closed 停止；
- **不放主动技能**（推后 V2）；决策层做成可插拔插槽（以后可换 LLM）。

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
| 编排 | `battle/runtime/` | core + perception + execution | 间接 | 需 Fake/设备 |
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
├── perception/                  ⚠️ 逻辑已实现，识别节点/ROI 待标定
│   ├── config.py                节点名模板 + 阈值（ROI 在 resource，TODO）
│   └── perception.py            build(context, img) -> BattleState
├── execution/                   ⚠️ 逻辑已实现，坐标/确认节点待标定
│   ├── coords.py                1280×720 坐标表（全为 (0,0) 占位 TODO）
│   └── executor.py              Executor：select_enemy/card/np/attack + 后置确认
└── runtime/                     ✅ 已实现
    └── runtime.py               AutoBattleRuntime + BattleResult

agent/custom/auto_battle_action.py   ✅ 已实现（save_evidence 为 TODO）
agent/main.py                        ✅ 已追加 `import auto_battle_action` 注册
tests/battle/test_core.py            ✅ 已写，尚未运行
```

图例：✅ 逻辑完整；⚠️ 代码完整但依赖尚未创建的 resource 节点 / 未标定坐标。

---

## 4. 一个回合的调用链

`AutoBattleRuntime.run()` 主循环（`turns < max_turns`）：

```
controller.post_screencap().wait().get()            # 截图
  → perception.build(context, img) -> BattleState    # 一帧多识别
  → 场景分支:
       VICTORY                         -> BattleResult.success
       DEFEAT/DIALOG/UNKNOWN           -> BattleResult.fail(unsafe_scene:…)
       非 COMMAND_SELECTION            -> wait_freezes(300)，累计等待，超 60 次判 stuck
       COMMAND_SELECTION               -> 继续
  → decider.decide(state) -> BattleAction
  → validate(action, state, profile) -> Verdict       # 不通过即 fail
  → _execute_turn(action):                            # 逐原子动作 + 后置确认
       select_enemy(target)   （若有）
       for pick: select_np / select_card
       attack
       任一确认失败 -> False -> BattleResult.fail(execution_confirm_failed)
  → turns += 1
```

任何一步失败都停止（fail-closed），不盲目重试。

---

## 5. 领域契约（`core/models.py`）

不可变 `@dataclass(frozen=True)`，坐标绝不出现在契约里。

- `Confidence(value, source)`，`passes(threshold)`。
- `CommandCard(ui_slot 1..5, color, owner_slot|None, confidence)` —— 下排面卡；**V1 `owner_slot` 恒为 None**。
- `NpCard(servant_slot 1..3, confidence)` —— 上排宝具卡。
- `EnemyState(slot, alive, targeted, confidence)`。
- `BattleState(scene, scene_confidence, cards[5], np_cards[0..3], enemies, screenshot_id, schema_version, unknown_fields)`
  - `command_ready(0.95)`：场景为选卡且置信度达标。
  - `cards_ready(0.90)`：恰 5 张面卡且每张达标。
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

`validate(action, state, profile) -> Verdict(ok, reason)`，任何来源的 action 都必须过。拒绝原因：

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

---

## 8. 感知实现（`perception/perception.py`）

`build(context, img, screenshot_id) -> BattleState`：一次截图，在同一帧上跑多个识别节点拼状态。

- **场景**：依次跑 `config.SCENE_NODES` 中各节点，命中即定场景，皆不中为 `UNKNOWN`。
- **卡色**：每张卡跑 `战斗_卡{i}_{B|A|Q}` 三个 **ColorMatch** 节点，比 `best_result.count`（命中像素数）argmax，置信度 = 赢家/总和；全 0 则置信度 0（标 `unknown_fields`）。
- **宝具卡**：`战斗_NP卡{1..3}` **TemplateMatch** 命中即视为该从者 NP 满。
- **敌人**：`战斗_敌人{slot}` 判存活、`战斗_敌人{slot}_选中` 判当前目标。

**现状**：调用逻辑完整；但上述**识别节点尚未在 MFW resource 中创建，ROI/HSV 阈值需用真实 1280×720 截图标定**（见 `config.py` 的 TODO）。API 字段已按 MaaFw 5.12.2 核对（`RecognitionDetail.hit` / `best_result.count|score`）。

---

## 9. 执行实现（`execution/executor.py`）

`Executor(context)`：`controller = context.tasker.controller`。

- 原子动作：`select_enemy` / `select_card` / `select_np` / `attack`，各为"点击坐标 + 后置确认识别节点"。
- **硬禁区**：类中**故意不提供** 令咒/圣晶石复活/氪金/抽卡/补 AP 入口——决策层无法触及。
- 后置确认：点击后重新截图跑确认节点，`hit` 为真才算成功；失败返回 False（上层停止），不盲点。

**现状**：`coords.py` 坐标全为 `(0,0)` 占位、确认节点名（`战斗_已选卡计数变化` 等）尚未创建，均待截图标定。选卡确认目前是"命中某节点"，真实实现可能需比较点击前后已选卡计数（已在注释标注）。

---

## 10. 回合状态机（`runtime/runtime.py`）

`AutoBattleRuntime(context, decider, profile)`，`run() -> BattleResult`。

- 场景收敛：VICTORY→success；DEFEAT/DIALOG/UNKNOWN→fail(`unsafe_scene:*`)。
- 非选卡场景：`context.wait_freezes(300)` 等稳，连续超过 `_MAX_NONCOMMAND_WAITS=60` 次→fail(`stuck_non_command`)。
- 每回合：decide→validate（拒绝→fail(`action_rejected:*`)）→`_execute_turn`（确认失败→fail(`execution_confirm_failed`)）→`turns++`。
- 超过 `profile.max_turns`→fail(`max_turns_exceeded`)。

`BattleResult(ok, reason, turns)`，含 `success()/fail()` 便捷构造。

---

## 11. MFW 集成（`custom/auto_battle_action.py` + `main.py`）

- 注册：`@AgentServer.custom_action("auto_battle")`；`agent/main.py` 追加 `import auto_battle_action`。
- 因 `main.py` 只把 `agent/custom` 加入 `sys.path`，本文件在导入前把 `agent/` 目录加入 `sys.path`，使 `battle` 可作顶层包导入。
- 参数：读 `argv.custom_action_param`（JSON），支持 `strategy_profile` / `max_turns`。
- 返回 `CustomAction.RunResult(success=result.ok)`。
- Pipeline 节点示例见整合版文档第 10 节；节点动作部分可直接建，识别子节点待 ROI。

---

## 12. 当前状态与待办

**可用（无需设备）**：`core` 全套（契约/决策/校验），逻辑完整。

**待真实 1280×720 截图标定后才能跑通**：
- `perception/config.py` 的识别节点 ROI/阈值 + 在 resource 创建对应节点；
- `execution/coords.py` 的点击坐标；
- 执行确认节点。

**其它 TODO**：
- `auto_battle_action.py` 的 `save_evidence`（失败存证据）未实现；
- `tests/battle/test_core.py` 已写未运行；
- 回合循环尚未做可测性改造（当前硬调 perception/executor，无设备难测）。

---

## 13. 如何扩展

- **接入 LLM**：新增 `LLMDecider` 实现 `Decider.decide`，在 `auto_battle_action.py` 里替换 `RuleDecider`；感知/执行/校验/循环全不动。
- **V2 主动技能/NP 顺序**：给 `BattleAction` 补 `servant_skills` 等字段，扩 `Executor` 与 `Validator`，感知加技能可用性识别，并接入从者数据。
- **标定 ROI/坐标**：拿 MFW 截的 720p 图 → 定 5 面卡/3 宝具卡/敌方槽位/攻击钮坐标 → 在 resource 建卡色 ColorMatch / 场景 TemplateMatch 等节点 → 用黄金集验到目标准确率。

---

## 14. 已知限制

- 仅 V1b：不放主动技能，`owner_slot` 未识别（禁用 Brave/从者优先级）。
- 单服/单分辨率（1280×720）优先；多服/多 UI 版本未覆盖。
- 未接入伤害/NP 模拟，选卡为启发式规则而非估值。
- 高风险动作靠"执行层不提供入口"硬性排除，非靠配置开关。
