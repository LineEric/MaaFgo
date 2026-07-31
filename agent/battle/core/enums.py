"""战斗领域枚举。纯 stdlib，禁止 import maa/cv2/socket。"""
from enum import Enum


class Scene(str, Enum):
    COMMAND_SELECTION = "command_selection"
    ANIMATION = "animation"
    VICTORY = "victory"
    DEFEAT = "defeat"
    DIALOG = "dialog"
    UNKNOWN = "unknown"


class CardColor(str, Enum):
    BUSTER = "B"
    ARTS = "A"
    QUICK = "Q"


class PrimitiveKind(str, Enum):
    SELECT_ENEMY = "select_enemy"
    SELECT_CARD = "select_card"   # 下排面卡
    SELECT_NP = "select_np"       # 上排宝具卡
    ATTACK = "attack"
    STOP = "stop"
