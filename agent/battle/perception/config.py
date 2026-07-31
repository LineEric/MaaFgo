"""感知配置：识别节点名与阈值。

约定：真正的 ROI/HSV 阈值写在 MFW resource 的识别节点里（1280x720 下标定），
本文件只保存"节点名模板"和门控阈值。节点尚未在 resource 中定义，需用真实截图标定后创建。

TODO(校准)：在 assets/resource/base/pipeline/ 下新建战斗识别节点：
  - 战斗_选卡场景        TemplateMatch（攻击按钮）
  - 战斗_胜利 / 战斗_失败 / 战斗_未知弹窗   TemplateMatch(+OCR)
  - 战斗_卡{1..5}_{B|A|Q} ColorMatch(HSV) —— 每卡 ROI 三色
  - 战斗_NP卡{1..3}       TemplateMatch（上排宝具卡框）
  - 战斗_敌人{1..3} / 战斗_敌人{1..3}_选中
"""
from __future__ import annotations

# 场景检测节点（按序尝试，先命中者定场景）
SCENE_NODES = {
    "command_selection": "战斗_选卡场景",   # TemplateMatch：选卡界面特征（如卡区/返回钮）
    "main_battle": "战斗_主界面",            # TemplateMatch：主界面攻击钮
    "victory": "战斗_胜利",
    "defeat": "战斗_失败",
    "dialog": "战斗_未知弹窗",
}

# 参数化节点名模板
CARD_COLOR_NODE = "战斗_卡{ui_slot}_{color}"     # ColorMatch，取 best_result.count 比大小
NP_CARD_NODE = "战斗_NP卡{servant_slot}"          # TemplateMatch
ENEMY_NODE = "战斗_敌人{slot}"                     # 存活/位置
ENEMY_TARGET_NODE = "战斗_敌人{slot}_选中"         # 当前目标

# 门控阈值
MIN_SCENE_CONFIDENCE = 0.95
MIN_CARD_CONFIDENCE = 0.90

# V1 前排/敌方槽位数（先按最多算，实际存活由识别决定）
FRONTLINE_SLOTS = (1, 2, 3)
ENEMY_SLOTS = (1, 2, 3)
