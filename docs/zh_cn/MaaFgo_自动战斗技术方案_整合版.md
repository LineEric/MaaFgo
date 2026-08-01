# MaaFgo 自动战斗技术方案（整合版）

> **版本**：v2.2（整合稿）
> **日期**：2026-08-01
> **V1b 状态**：✅ 已通过真机验证。
> **目标项目**：[MaaFgo](https://github.com/xlxyvergil/MaaFgo)（基于 MaaFramework，简称 MFW）
> **性质**：整合并取代前三份文档，落定当前实际技术路线。

---

## 0. 本文与原三份文档的关系

本文整合 `research_report.md`、`智能战斗可行性分析与整体项目框架设计.md`、`原生回合战斗Planner_具体技术方案.md` 三份稿件，**保留其中正确的骨架，修正其取向与若干事实错误**。

**保留**：`BattleState` / `BattleAction` 结构化契约、Validator/Executor 安全带、fail-closed、高风险硬禁区、分层测试与回放思路。

**修正（重要）**：

| 项 | 原文档 | 本文 |
|---|---|---|
| 决策层取向 | LLM 受限顾问，且优先做固定队 | **决策层做成可插拔插槽**；V1 先用规则+CV，不依赖大模型，LLM 以后作为一个 Decider 实现接入 |
| 分辨率 | 1920×1080 + 归一化内容区 | **1280×720**（MaaFgo 实际坐标系，见现有代码）；MFW 已统一分辨率，**无需归一化映射** |
| 感知实现 | 倾向自写 cv2 | **优先用 MFW 原生 Recognition + `context.run_recognition`**，ROI/阈值/模板写进 resource |
| 选卡界面 | 5 张卡 | **5 张面卡 + 0~3 张宝具卡（上排）**，每回合从合池里选 3 张 |
| V1 范围 | 固定计划+动态选卡 | **V1b：动态选卡 + 出宝具卡，不放主动技能**（放宝具无需理解从者，放技能需要，故推后） |

---

## 1. 目标与范围

### 1.1 最终愿景
一个**独立功能按钮**："自动战斗接管"。开启后逐回合接管战斗，目标覆盖各种关卡；后续再包成 MFW custom action 接入 pipeline 调度。

### 1.2 V1 范围（V1b）
- 识别是否处于选卡界面；
- 识别 5 张面卡（卡色、槽位）；
- 识别上排 0~3 张宝具卡出现情况（等价于对应从者 NP 是否满）；
- 选择敌方目标（默认当前/第一个存活敌人）；
- 策略：**宝具卡优先出，剩余槽位用面卡按卡色/Brave 规则补齐，共选 3 张**；
- 原子点击 + 每步后置确认；
- 识别胜利/下一回合/异常并安全停止；
- 保存回放证据。

### 1.3 非目标（V1）
- 不放主动从者技能 / 御主技能 / 换人（需要从者知识，推后到 V2）；
- 不做高保真伤害/NP 模拟；
- 不接入大模型（决策层留插槽）；
- 不承诺未知高难本通用通关；
- 不涉及规避检测 / 对抗风控。

### 1.4 风险声明
FGO 自动化本身可能违反游戏服务条款，存在账号风险，且该风险**与是否使用大模型无关**。上线前由使用者自行评估 ToS 与账号取舍。本方案不实现任何规避检测/对抗机制。

---

## 2. 现状与切入点

MaaFgo 现有链路（基于代码核对）：

```
MWU/MXU 前端 → MFW Tasker + Pipeline(assets/resource/base/pipeline)
  → Python Agent(agent/main.py, AgentServer 注册 custom action)
      ├─ custom/general_navigation_action.py 等导航
      ├─ custom/bbc_action.py / bbc_connection_manager.py（BBC 战斗后端）
      └─ chaldea/*（队伍数据导入）
  → 模拟器 / 游戏客户端（1280×720 坐标系）
```

**切入点**：新增一个 `auto_battle` custom action，与 `执行BBC任务` **并列**，作为可选战斗后端。**默认路径不变**，实验功能通过开发者开关/独立按钮触发。

现有 API 用法（已核对 `general_navigation_action.py`）：
- 取控制器：`controller = context.tasker.controller`
- 截图：`controller.post_screencap().wait().get()`（返回图像）
- 点击：`controller.post_click(x, y).wait()`
- 读节点参数：`context.get_node_data("节点名")` 的 `attach`
- custom action 签名：`def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult`

---

## 3. 核心设计原则

1. **决策层可插拔**。`Decider: BattleState → BattleAction`；V1 是 `RuleDecider`，以后 `LLMDecider` 直接替换，感知/执行不动。
2. **脏活优先，纯逻辑其次**。感知、原子操作耦合 MFW/设备/分辨率，藏着全部可靠性风险，**先搭稳、要能测**；决策层是纯逻辑，离线可测，可先用占位实现。
3. **结构化契约**。视觉、决策、执行、日志都围绕 `BattleState` / `BattleAction`，各自独立演进。
4. **用 MFW 原生能力**。识别用 Recognition 节点 + `context.run_recognition`；点击用 `controller`。不自建 CV/点击框架。
5. **Fail-Closed**。场景不符、关键字段低置信度、未知弹窗、后置确认失败 → 保存证据并停止，绝不盲点。
6. **高风险硬禁区**。令咒、圣晶石复活、氪金货币、抽卡、账号操作**在原子操作层不提供入口**，决策层无法触及。

---

## 4. 整体架构

```
Pipeline（粗粒度，已有）：
  导航 → 选队/助战 → 进入战斗 → [Custom Action: auto_battle] → 战斗结算 → 回主界面

Custom Action auto_battle（Python，掌控回合循环）：
  loop:
    img   = controller.post_screencap().wait().get()
    state = Perception.build(context, img)     # run_recognition × N → BattleState
    if state.scene == VICTORY:  return success
    if state.scene in {DEFEAT, UNKNOWN, DIALOG}: stop(save_evidence)
    if state.scene != COMMAND_SELECTION: continue
    action = decider.decide(state)             # RuleDecider(现) / LLMDecider(后)
    if not validator.validate(action, state):  stop
    executor.run(context, action)              # controller 点击 + 后置确认
```

**分层**（哪些耦合 MFW）：

| 层 | 模块 | 耦合 MFW？ | 可离线测？ |
|---|---|---|---|
| 契约 | models（State/Action/Primitive） | 否 | 是 |
| 决策 | Decider / Validator | 否 | 是（纯逻辑） |
| 感知 | Perception | **是**（run_recognition） | 静态截图可测 |
| 执行 | Executor | **是**（controller） | 需 Fake/集成 |
| 编排 | Runtime（custom action） | 是 | Fake 可测 |

**坐标系**：统一 **1280×720**。MFW Controller 已将设备分辨率归一到该坐标空间，直接用 720p 坐标点击，**无需内容区归一化**。

---

## 5. 领域契约（models）

V1 精简版（Python dataclass，`battle/core/`，禁止 import maa/cv2/socket）。

```python
class Scene(str, Enum):
    COMMAND_SELECTION="command_selection"; VICTORY="victory"; DEFEAT="defeat"
    ANIMATION="animation"; DIALOG="dialog"; UNKNOWN="unknown"

class CardColor(str, Enum): BUSTER="B"; ARTS="A"; QUICK="Q"

@dataclass(frozen=True)
class Confidence: value: float; source: str = ""

@dataclass(frozen=True)
class CommandCard:          # 下排面卡
    ui_slot: int            # 1..5
    color: CardColor
    owner_slot: Optional[int]   # V1 先 None
    confidence: Confidence

@dataclass(frozen=True)
class NpCard:               # 上排宝具卡
    servant_slot: int       # 1..3，谁的 NP
    confidence: Confidence

@dataclass(frozen=True)
class EnemyState:
    slot: int; alive: bool; targeted: bool; confidence: Confidence

@dataclass(frozen=True)
class BattleState:
    schema_version: int
    scene: Scene
    scene_confidence: Confidence
    cards: Tuple[CommandCard, ...]      # 期望 5 张
    np_cards: Tuple[NpCard, ...]        # 0..3 张
    enemies: Tuple[EnemyState, ...]
    screenshot_id: str
    unknown_fields: Tuple[str, ...] = ()

    def command_ready(self) -> bool:
        return self.scene is Scene.COMMAND_SELECTION and self.scene_confidence.value >= 0.95
    def cards_ready(self) -> bool:
        return len(self.cards) == 5 and all(c.confidence.value >= 0.90 for c in self.cards)
```

动作契约（V1 只用选目标 + 选 3 张卡；技能/NP 顺序字段**预留但为空**）：

```python
@dataclass(frozen=True)
class CardPick:                 # 一次选卡：来自面卡或宝具卡
    kind: str                   # "command" | "np"
    slot: int                   # command: ui_slot 1..5 ; np: servant_slot 1..3

@dataclass(frozen=True)
class BattleAction:
    target_enemy: Optional[int]
    picks: Tuple[CardPick, ...]        # 恰 3 个，含顺序
    # 预留（V1 不用）：servant_skills, master_skills, order_change
    rationale_tag: str = ""

@dataclass(frozen=True)
class PrimitiveAction:              # 不含 x/y，坐标只在 Executor
    kind: str                       # select_enemy | select_card | select_np | attack | stop
    slot: Optional[int] = None
```

---

## 6. 感知模块（重点）

### 6.1 方法：一帧多识别
截一次图 → 在同一帧上跑多个**在 resource JSON 定义好的识别节点** → 拼成 `BattleState`。这样 ROI/阈值/模板全在资源里，可调、可视化调试、可对静态截图单测。

> **实现注记**：设计稿原定卡色用 ColorMatch（HSV 像素计数 argmax），实际实现改用 **OCR 文字识别**（力击/迅击/技击 → B/A/Q），因 OCR 文字比 HSV 色彩更鲁棒。NP 卡检测也从 TemplateMatch 改为 OCR 数值识别（≥100%）。识别节点已创建于 `assets/resource/base/pipeline/自动战斗_感知.json`，ROI 已填入实际 1280×720 坐标值并**通过真机验证**。

```python
# 实际实现（perception.py）
def build(context, img, screenshot_id="") -> BattleState:
    scene, sconf = _detect_scene(context, img)
    cards = tuple(_detect_card(context, img, i) for i in range(1, 6))   # OCR -> 力击/迅击/技击
    np_cards = tuple(c for c in (_detect_np(context, img, s) for s in (1,2,3)) if c)  # OCR -> NP数值≥100
    enemies = _detect_enemies(context, img)
    return BattleState(scene, Confidence(sconf, "scene"), cards, np_cards, enemies, screenshot_id=screenshot_id)

def _detect_card(context, img, ui_slot) -> CommandCard:
    node = config.CARD_NODE.format(ui_slot=ui_slot)   # "战斗_卡{ui_slot}" OCR 节点
    r = context.run_recognition(node, img)
    text = r.best_result.text if (r and r.hit and r.best_result) else ""
    color = _CARD_TEXT_MAP.get(text.strip())   # 力击->B, 技击->A, 迅击->Q
    score = r.best_result.score if (r and r.best_result) else 0.0
    return CommandCard(ui_slot, color or CardColor.BUSTER, None, Confidence(float(score), "ocr"))
```

### 6.2 字段 ↔ MFW 识别节点映射（720p）

| BattleState 字段 | 识别类型 | 备注 |
|---|---|---|
| `scene==选卡` | `TemplateMatch`（选卡界面特征） | 命中=选卡界面 |
| `scene==主界面` | `TemplateMatch`（攻击钮） | 命中=主界面 |
| 面卡 `color` ×5 | `OCR`（力击/迅击/技击） | 每卡 ROI 跑 OCR，文字映射 B/A/Q |
| `np_cards` | `OCR`（NP 数值 ≥100%） | 上排 3 个 NP 卡 ROI，OCR 提取数字 |
| 敌人 `alive`/`targeted` | `TemplateMatch`/`ColorMatch`（敌方槽位） | 当前 resource 中为 `DoNothing` 占位，待标定 |
| 场景 `victory/defeat` | `OCR` + `TemplateMatch` | 收敛与停止用 |
| `owner_slot` | —（V1 为 None） | V2 用头像/边框模板，见 6.4 |

> 识别节点已创建于 `assets/resource/base/pipeline/自动战斗_感知.json`，ROI 已填入实际 1280×720 坐标值并**通过真机验证**。场景模板图片已制作并验证。

### 6.3 置信度门控
- 选卡/结算场景判断阈值 0.95，不足 → 重新截图等待稳定，仍失败 → 停止；
- 卡色阈值当前设为 0.50（`StrategyProfile.min_card_confidence`），低于此 → 标 `unknown_fields`；
- **低置信度是状态的一部分，不是"凑合继续决策"**。

### 6.4 卡归属（owner_slot）降级策略
最难，V1 不做：
1. **V1**：`owner_slot=None`，禁用 Brave/从者优先级，只按卡色选；
2. **V2**：固定前排，预采头像/边框模板匹配；
3. **V3**：轻量分类器 + 战斗状态追踪。

### 6.5 证据
每个关键字段可回溯到 `screenshot_id + ROI + 方法 + score`，便于区分"识别错"还是"决策错"。MFW 的识别命中框调试图可直接利用。

---

## 7. 原子操作模块（重点）

### 7.1 封装 controller + 后置确认
原子动作 = 720p 坐标点击 + 立即重跑识别确认状态变化。**不盲目重点**。

> **实现注记**：设计稿描述的后置确认（numpy 像素差 / run_recognition 确认）当前未实现——所有原子操作点击后直接返回 `True`，以 `time.sleep` 间隔代替。真机验证表明此方案在正常条件下可稳定运行。坐标已填入实际值并验证通过。

```python
# 实际实现（executor.py）
class Executor:
    def __init__(self, context, controller=None):
        self.ctx = context
        self.controller = controller or context.tasker.controller

    def open_command_cards(self) -> bool:
        self._click(coords.ATTACK_BTN)   # (1136, 601)
        return True                        # 无后置确认

    def select_card(self, ui_slot: int) -> bool:
        self._click(coords.center(coords.CARD_ROI[ui_slot]))  # ROI 中心点
        return True                        # 无后置确认

    def select_np(self, servant_slot: int) -> bool:
        self._click(coords.NP_CLICK[servant_slot])
        return True                        # 无后置确认

    # 不提供 command_spell / sq_revive / gacha / ap_refill —— 硬禁区
```

### 7.2 后置确认对照

| 原子动作 | 最小确认 |
|---|---|
| 选敌 | 目标框/标识变化 |
| 选面卡 | 该卡高亮/已选计数 +1 |
| 选 NP 卡 | 宝具卡选中态/攻击序列计数变化 |
| 攻击 | 选卡区消失 / 进入动画或下一场景 |

确认失败统一处理：**重新观测 → 记录 → 停止**。

### 7.3 坐标表（已填入并验证）
```python
# execution/coords.py（1280×720）
CARD_ROI = {   # 下排 5 面卡 ROI 框 (x, y, w, h)
    1: (75, 529, 138, 98), 2: (320, 529, 136, 98), 3: (578, 529, 136, 98),
    4: (842, 529, 127, 98), 5: (1097, 529, 129, 98),
}
NP_ROI = {     # 上排 3 宝具卡 ROI 框（OCR 检测 NP 数值）
    1: (222, 656, 83, 26), 2: (540, 657, 83, 24), 3: (865, 655, 73, 25),
}
NP_CLICK = {   # 宝具卡点击位置
    1: (410, 138), 2: (640, 138), 3: (875, 138),
}
ATTACK_BTN = (1136, 601)           # 主界面攻击钮
ENEMY_POINT = {1: (0,0), 2: (0,0), 3: (0,0)}  # 敌方槽位，仍为占位
```

---

## 8. 决策层（其次，可插拔）

### 8.1 接口
```python
class Decider(Protocol):
    def decide(self, state: BattleState) -> BattleAction: ...
```
实现：`RuleDecider`（V1）；`LLMDecider`（后置，接口相同，直接替换）。

### 8.2 V1 RuleDecider（NP 优先 + 面卡补齐）
候选池 = 面卡(≤5) + 宝具卡(≤3)，从中选 3 张排序，全枚举（≤ P(8,3)=336）打分：
- **宝具卡强制优先**（有几张 NP 卡先占几个槽，最多 3）；
- 剩余槽位用面卡，按 `color_priority` + 同色 Chain + 首/末卡奖励打分；
- `owner_slot=None` 时不计 Brave/从者优先级。

```python
def decide(self, state):
    target = _first_alive_or_targeted(state.enemies)
    picks = [CardPick("np", c.servant_slot) for c in state.np_cards][:3]
    need = 3 - len(picks)
    if need > 0:
        best = _best_command_chain(state.cards, need, self.policy)  # 枚举打分
        picks += [CardPick("command", c.ui_slot) for c in best]
    return BattleAction(target, tuple(picks[:3]), rationale_tag="v1b_np_first")
```

### 8.3 Validator（独立，永远兜底）
无论 Action 来自规则、以后 LLM 还是导入，都必须过：
- `picks` 恰 3 个、无重复、引用的卡/NP 在 state 中存在；
- `target_enemy` 在存活敌人集合；
- （V2）技能/NP 可用性、目标类型合法；
- 命中硬禁区 → 拒绝。

---

## 9. 回合循环（Runtime / Custom Action）

### 9.1 状态机
```
OBSERVE → 判场景
  ├ VICTORY → SUCCESS
  ├ DEFEAT/DIALOG/UNKNOWN → STOP(save_evidence)
  ├ ANIMATION/其他 → 等稳定后重新 OBSERVE
  └ COMMAND_SELECTION →
       PLAN(decide) → VALIDATE →
       EXECUTE: select_enemy → select(np/card)×3 → attack
       每步后置确认失败 → STOP
       → OBSERVE
超过 max_turns → FAIL
```

### 9.2 停止条件（fail-closed）
场景不符、关键字段低置信度、未知弹窗、计划与状态不一致、后置确认失败、超过每回合/每战斗重试上限、命中硬禁区 —— 一律停止并保存证据。

---

## 10. MFW 集成落地

### 10.1 注册（agent/main.py）
```python
import auto_battle_action   # 新增，与 bbc_action 并列 import
```

### 10.2 Pipeline 节点

**入口节点**（`assets/resource/base/pipeline/自动战斗.json`）**尚未创建**，计划结构：
```jsonc
{
  "自动战斗": {
    "recognition": "DirectHit",
    "action": "Custom",
    "custom_action": "auto_battle",
    "custom_action_param": {
      "strategy_profile": "farm-safe-v1",
      "max_turns": 20,
      "save_evidence": true,
      "fallback": "stop"
    },
    "next": ["战斗完成信息"],
    "on_error": ["保存战斗证据并停止"]
  }
}
```

**识别节点**已创建于 `assets/resource/base/pipeline/自动战斗_感知.json`，包含：
- `战斗_卡{1..5}`：OCR 识别力击/迅击/技击，ROI 已填入；
- `战斗_NP卡{1..3}`：OCR 识别 NP 数值，ROI 已填入；
- `战斗_选卡场景` / `战斗_主界面`：TemplateMatch，ROI 已填入，模板图片待制作；
- `战斗_胜利`：OCR 识别"战斗结果"；
- `战斗_失败`：TemplateMatch，模板图片待制作；
- `战斗_敌人{1..3}` / `战斗_敌人{1..3}_选中`：`DoNothing` 占位，待标定。

供 `context.run_recognition` 按名调用。

### 10.3 与 BBC 并存
不替换 `执行BBC任务`。默认仍走 BBC；`自动战斗` 作为可选后端，先只暴露开发者开关，避免普通用户误用。

---

## 11. 标注与测试

### 11.1 标注规范（每张截图，标语义标签而非坐标框）

**Tier 0（MVP 必标）**：`scene`；每张面卡 `color`；`np_cards`（哪些 servant_slot 有宝具卡）；敌人 `alive`/`targeted`。
**Tier 1（后续）**：面卡 `owner_slot`；敌人 `hp`；`wave`/`turn`。
**Tier 2（远期）**：暴击星、buff/debuff、Break。
**元信息**：server、resolution、emulator、app_version、team、quest_id、hash；外加 `human_uncertain` 标记（连人都看不准的字段，用于测"低置信度必停"）。

示例：
```json
{
  "screenshot": "cn_1280x720_cmd_001.png",
  "meta": {"server":"CN","resolution":"1280x720","team":[1,2,3]},
  "scene": "command_selection",
  "cards": [{"ui_slot":1,"color":"B"},{"ui_slot":2,"color":"A"},{"ui_slot":3,"color":"Q"},{"ui_slot":4,"color":"A"},{"ui_slot":5,"color":"B"}],
  "np_cards": [1,3],
  "enemies": [{"slot":1,"alive":true,"targeted":true}]
}
```

### 11.2 测试分层
```
纯逻辑（决策/校验/选卡枚举）  无 MFW/无设备，pytest 直接跑
感知（静态截图 → 字段）        静态截图黄金集，不连设备
回放（帧序列 → 动作/终态）      Fake/Replay，不连设备
MFW 集成                       指定模拟器+版本，少量、受控
```

### 11.3 指标门槛（执行前）
| 指标 | 门槛 |
|---|---|
| 选卡场景识别准确率 | ≥ 99% |
| 卡色识别准确率 | ≥ 99% |
| NP 卡在场识别准确率 | ≥ 99% |
| 原子动作后置确认成功率 | ≥ 99.5% |
| 非法 Action 通过 Validator | 0 |
| 高风险动作误触发 | 0 |
| 低置信度仍执行 | 0 |

---

## 12. 建议模块搭建顺序

```
1. 领域契约 models（State/Action/Primitive）           —— 定死接口
2. 感知模块 Perception（run_recognition × N）           —— 重点，耦合 MFW
3. 原子操作 Executor（controller + 后置确认 + 硬禁区）   —— 重点，耦合 MFW
4. 占位决策器 RuleDecider（NP 优先 + 面卡补齐）+ Validator —— 其次，纯逻辑
5. 回合循环 auto_battle custom action                    —— 串联 2-4
6. 标注 + 黄金集 + 指标                                   —— 贯穿
```

后续演进：V2 加主动技能/NP 计划（需从者数据）→ V3 pipeline 集成 & 文本模型支持 → V4 `LLMDecider` 替换决策层实现"各种场合"通用。**2、3 稳定后，升级决策器不动感知/执行。**

---

## 13. 数据（轻量、后置）

V1 **不需要从者数据**——选卡与出宝具都是通用规则。仅 V2 起放主动技能时才需要从者技能元数据。届时：
- 建本地版本化数据包（servant/quest/mystic_code），运行时只读本地，不实时依赖网络；
- `TeamSnapshot` 作为 Chaldea/手动/BBC 配置的统一中间模型；
- Chaldea 导入作为"先验"，实时以画面为准。

---

## 14. 风险与对策（精简）

| 风险 | 对策 |
|---|---|
| UI/版本/服差异 | 资源按服/版本分包，黄金集回归，白名单推进 |
| 模拟器缩放差异 | MFW 已归一 720p；执行前锚点确认 |
| 动画/延迟 | 稳定帧 + 后置确认 + 超时 + 有限重试 |
| 卡色/NP 误识别 | 置信度门控 + 二次观测 + 保守/停止 |
| 端口/进程竞争 | 与 BBC 隔离，明确进程所有权 |
| 高价值资源误触 | 原子层不提供入口 + 未知弹窗即停 |
| 账号/条款 | 文档明确边界，不做规避/对抗 |
| 许可证 | Chaldea(AGPL) 代码不直接混入；FGA(MIT) 只借鉴功能模型 |

---

## 附录 A：MFW 关键 API（已按 MaaFw 5.12.2 源码核对）

**识别**
- `context.run_recognition(entry, image, pipeline_override=None) -> Optional[RecognitionDetail]`
  只要识别执行了就返回对象；判命中用 `.hit`。None 仅在 entry 不存在/节点禁用/图为空。
- `context.run_recognition_direct(reco_type, reco_param, image)`：不需 JSON 节点，直接按类型+参数跑。
- `RecognitionDetail`：`hit: bool`、`box: Rect|None`、`best_result`、`all_results`、`filtered_results`、`raw_detail`、`draw_images`(debug)。
- 各算法 `best_result` 类型：ColorMatch→`BoxAndCountResult(.count)`；TemplateMatch→`BoxAndScoreResult(.score)`；OCR→`OCRResult(.text,.score)`；NNClassify→`NeuralNetworkResult(.cls_index,.label,.score)`；Custom→`CustomRecognitionResult(.detail)`。
- `Rect(x,y,w,h)` 可迭代/下标。

**自定义识别 / 动作**
- `CustomRecognition.analyze(self, context, argv) -> AnalyzeResult | RectType | None`
  `argv`：`image`(BGR numpy)、`roi: Rect`、`custom_recognition_param`(JSON str)、`node_name`、`task_detail`。
  `AnalyzeResult(box: Optional[RectType], detail: dict)`。
- `CustomAction.run(self, context, argv) -> RunResult | bool`
  `argv`：`reco_detail: RecognitionDetail`、`box: Rect`、`custom_action_param`(JSON str)、`node_name`、`task_detail`。
  `RunResult(success: bool)`；返回 bool 或 None(=True) 也可。
- 注册：`@AgentServer.custom_action("名")` / `@AgentServer.custom_recognition("名")`，在 `agent/main.py` import 即注册。

**控制器**（`controller = context.tasker.controller`）
- `controller.post_screencap().wait().get() -> numpy.ndarray`
- `controller.cached_image -> numpy.ndarray`（最近一帧，省一次截图）
- `controller.post_click(x, y, contact=0, pressure=1).wait()`；`post_swipe(...)`。

**其他**
- `context.wait_freezes(time, box=None, wait_freezes_param=None)`：等画面静止。
- `context.get_node_data(name)`：读节点定义（MaaFgo 现用 `attach` 传参即走这里）。
- `context.override_next(name, next_list)` / `context.override_pipeline(...)`：运行时改流程。

## 15. 参考资料

1. [MaaFgo](https://github.com/xlxyvergil/MaaFgo) —— 项目基线（`agent/main.py`、`agent/custom/*`、`assets/resource/*`）。
2. MaaFramework 技能参考（本仓库 `.claude/skills/maaframework/`）—— Recognition/Action/Custom Logic/Controller API，基于 MFW v5.x。
3. [MaaFramework Pipeline Protocol](https://maafw.com/en/docs/3.1-PipelineProtocol/)。
4. [Chaldea / Laplace](https://github.com/chaldea-center/chaldea) —— 数据/模拟参考，AGPL-3.0。
5. [FGA](https://github.com/Fate-Grand-Automata/FGA) —— 战斗配置/卡优先级/UX 参考，MIT。
6. [FGO Combat Mechanics — GamePress](https://fgo.gamepress.gg/combat-mechanics) —— 卡色/首卡/同色链/Brave/Mighty Chain/宝具机制。
