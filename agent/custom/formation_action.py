# -*- coding: utf-8 -*-
"""Chaldea 自动编队。

该 Action 仅从编队界面开始工作：读取 Chaldea BattleShareData 后，先拖拽调整
已有本地从者与助战的位置，再打开从者选择页替换不匹配的本地从者。原生自动
战斗相关 Action 不依赖、也不修改本模块。
"""

import glob
import json
import os
import re
import sys
import time
import traceback

import cv2
import numpy as np

from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction

_CUSTOM_DIR = os.path.dirname(os.path.abspath(__file__))
_AGENT_DIR = os.path.dirname(_CUSTOM_DIR)
_PROJECT_DIR = os.path.dirname(_AGENT_DIR)
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from chaldea import fetch_share_data
import mfaalog


BASE_W, BASE_H = 1280, 720

# 用户提供的六个编队槽位（1280 x 720 基准坐标）。
SLOT_ROIS = (
    (41, 160, 187, 276),
    (241, 160, 187, 276),
    (440, 160, 187, 276),
    (654, 160, 187, 276),
    (854, 160, 187, 276),
    (1055, 160, 188, 276),
)

FACE_THRESHOLD = 0.58
SUPPORT_THRESHOLD = 0.75
SWAP_DRAG_DURATION = 1200  # ms；长按并拖至目标槽中心
SWAP_SETTLE_SECONDS = 1.0
SERVANT_REPLACE_VERIFY_TIMEOUT_SECONDS = 10.0
SERVANT_REPLACE_VERIFY_INTERVAL_SECONDS = 0.5
EMPTY_SLOT_STD_THRESHOLD = 25.0
EMPTY_SLOT_CHANNEL_DELTA_THRESHOLD = 6.0
FORMATION_CONFIRM_ROI = (724, 583, 232, 101)
FORMATION_CONFIRM_DELAY_SECONDS = 1.0
SERVANT_FILTER_BUTTON_ROI = (900, 90, 140, 80)
SERVANT_FILTER_BLUE_RATIO = 0.55
MAX_REORDER_OPS = 12
MAX_FIND_SERVANT_ROUNDS = 80
SWIPE_LIST_BEGIN = (600, 560)
SWIPE_LIST_END = (600, 200)

SUPPORT_TYPES = {"friend", "fixed", "npc"}
CLASS_TEMPLATE = {
    "saber": "剑士",
    "archer": "弓兵",
    "lancer": "枪兵",
    "rider": "骑兵",
    "caster": "魔术师",
    "assassin": "暗杀者",
    "berserker": "狂战士",
    "ruler": "裁定者",
    "avenger": "复仇者",
    "moonCancer": "月之癌",
    "alterEgo": "他人格",
    "foreigner": "降临者",
    "pretender": "伪装者",
    "shielder": "盾兵",
    "beast": "兽",
}


def _norm_img(image):
    if image is None:
        return None
    if hasattr(image, "to_numpy"):
        image = image.to_numpy()
    array = np.asarray(image)
    if array.size == 0:
        return None
    if array.ndim == 2:
        array = cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
    elif array.ndim == 3 and array.shape[2] == 4:
        array = array[:, :, :3]
    if array.ndim != 3 or array.shape[2] != 3:
        return None
    return array.astype(np.uint8, copy=True)


def _read_image(path):
    if not path or not os.path.isfile(path):
        return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


@AgentServer.custom_action("auto_formation_from_chaldea")
class AutoFormationFromChaldea(CustomAction):
    """将 Chaldea 队伍编入当前游戏编队。"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            self.context = context
            self.controller = context.tasker.controller
            node = context.get_node_data(argv.node_name) or {}
            source = str((node.get("attach") or {}).get("chaldea_import_source") or "").strip()
            if not source:
                self._fail("invalid_chaldea_team: 未提供 Chaldea 分享链接/ID")
                return CustomAction.RunResult(success=False)

            self._init_paths()
            self._init_scale()
            share_data, _quest_id, _team_id = fetch_share_data(source)
            expected = self._build_expected(share_data)
            if expected is None:
                return CustomAction.RunResult(success=False)
            self.expected = expected

            expected_support_count = sum(item["kind"] == "SUPPORT" for item in expected)
            if expected_support_count > 1:
                self._fail("invalid_chaldea_team: 当前编队仅支持一个助战槽")
                return CustomAction.RunResult(success=False)

            self._prepare_target_templates()
            self.list_view_prepared = False
            if not self._run_pipeline("自动编队-打开配置"):
                self._fail("not_on_formation_page: 未找到配置变更按钮")
                return CustomAction.RunResult(success=False)
            if not self._wait_for(self._in_formation_edit, 5.0):
                self._fail("not_on_formation_page: 点击配置变更后未进入编辑状态")
                return CustomAction.RunResult(success=False)

            current = self._detect_slots()
            if current is None:
                return CustomAction.RunResult(success=False)
            # Chaldea 未指定助战时，允许把当前已选助战替换为目标本地从者；
            # 只有 Chaldea 明确要求助战时，才必须保证当前存在且仅存在一位助战。
            if expected_support_count and sum(item["kind"] == "SUPPORT" for item in current) != expected_support_count:
                self._fail("support_count_invalid: 当前助战数量与 Chaldea 目标不一致")
                return CustomAction.RunResult(success=False)
            self._log_layout("初始", current)

            if not self._relocate_unexpected_support(current, expected_support_count):
                return CustomAction.RunResult(success=False)
            if not self._reorder_existing():
                return CustomAction.RunResult(success=False)
            if not self._replace_local_servants():
                return CustomAction.RunResult(success=False)

            current = self._detect_slots()
            if current is None:
                return CustomAction.RunResult(success=False)
            self._log_layout("最终复核", current)
            mismatch = self._first_mismatch(current)
            if mismatch is not None:
                self._fail(f"final_formation_mismatch: 槽位{mismatch + 1}未匹配")
                return CustomAction.RunResult(success=False)

            self._log_support_identity_if_possible(current)
            if not self._run_pipeline("自动编队-编队决定"):
                self._fail("formation_confirm_failed: 未能点击编队决定")
                return CustomAction.RunResult(success=False)
            self._confirm_formation_change_if_present()
            mfaalog.info("[自动编队] 编队完成，已点击编队决定")
            return CustomAction.RunResult(success=True)
        except Exception as exc:
            mfaalog.error(f"[自动编队] 异常: {exc}\n{traceback.format_exc()}")
            return CustomAction.RunResult(success=False)

    # ---------- Chaldea 队伍规格 ----------

    def _build_expected(self, share_data):
        if not isinstance(share_data, dict) or not isinstance(share_data.get("team"), dict):
            self._fail("invalid_chaldea_team: 导入数据缺少 team")
            return None
        team = share_data["team"]
        raw_slots = list(team.get("onFieldSvts") or [])[:3] + list(team.get("backupSvts") or [])[:3]
        raw_slots += [None] * (6 - len(raw_slots))
        expected = []
        for index, item in enumerate(raw_slots[:6]):
            if item is None:
                expected.append({"kind": "EMPTY", "svt_id": None, "slot": index})
                continue
            if not isinstance(item, dict):
                self._fail(f"invalid_chaldea_team: 槽位{index + 1}数据类型错误")
                return None
            support_type = str(item.get("supportType") or "").lower()
            svt_id = item.get("svtId")
            if support_type in SUPPORT_TYPES:
                expected.append({"kind": "SUPPORT", "svt_id": svt_id, "slot": index})
                continue
            if not isinstance(svt_id, int) or svt_id <= 0:
                self._fail(f"invalid_chaldea_team: 槽位{index + 1}没有有效 svtId")
                return None
            expected.append({"kind": "LOCAL", "svt_id": svt_id, "slot": index})
        return expected

    # ---------- 路径、截图与模板 ----------

    def _init_paths(self):
        config = self.context.get_node_data("资源包配置") or {}
        package = str((config.get("attach") or {}).get("resource_package") or "base").strip()
        layer = "cn" if package == "cn" else "base"
        roots = []
        for root in (_PROJECT_DIR, os.path.dirname(_PROJECT_DIR)):
            for current_layer in (layer, "base"):
                image_root = os.path.join(root, "assets", "resource", current_layer, "image")
                if os.path.isdir(image_root) and image_root not in roots:
                    roots.append(image_root)
                packaged_root = os.path.join(root, "resource", current_layer, "image")
                if os.path.isdir(packaged_root) and packaged_root not in roots:
                    roots.append(packaged_root)
        self.image_roots = roots
        self.narrow_dirs = [os.path.join(root, "NarrowFigures") for root in roots]
        self.face_dirs = [os.path.join(root, "servant_face") for root in roots]

    def _init_scale(self):
        self.sx = self.sy = 1.0
        screenshot = self._shot()
        if screenshot is not None:
            height, width = screenshot.shape[:2]
            self.sx = width / BASE_W
            self.sy = height / BASE_H
            mfaalog.info(
                f"[自动编队] 实际分辨率 {width}x{height}，坐标缩放 {self.sx:.3f}x{self.sy:.3f}"
            )

    def _shot(self):
        return _norm_img(self.controller.post_screencap().wait().get())

    def _scale_roi(self, roi):
        x, y, width, height = roi
        return (
            int(round(x * self.sx)), int(round(y * self.sy)),
            int(round(width * self.sx)), int(round(height * self.sy)),
        )

    def _slot_center(self, index):
        x, y, width, height = SLOT_ROIS[index]
        return int(round((x + width / 2) * self.sx)), int(round((y + height / 2) * self.sy))

    def _template_path(self, relative):
        for root in self.image_roots:
            candidate = os.path.join(root, relative.replace("/", os.sep))
            if os.path.isfile(candidate):
                return candidate
        return ""

    def _load_named_template(self, relative):
        return _read_image(self._template_path(relative))

    def _load_servant_templates(self, svt_id, directories):
        sid = str(svt_id)
        if len(sid) <= 2:
            return []
        prefix = sid[:-2]
        matcher = re.compile(rf"^f_{re.escape(prefix)}\d{{3}}d?\.png$", re.IGNORECASE)
        result, seen = [], set()
        for directory in directories:
            for path in glob.glob(os.path.join(directory, "f_*.png")):
                name = os.path.basename(path)
                if name in seen or not matcher.match(name):
                    continue
                template = _read_image(path)
                if template is not None:
                    result.append((name, template))
                    seen.add(name)
        return result

    def _prepare_target_templates(self):
        self.local_templates = {}
        self.support_templates = {}
        for item in self.expected:
            svt_id = item["svt_id"]
            if item["kind"] == "LOCAL" and svt_id not in self.local_templates:
                templates = self._load_servant_templates(svt_id, self.narrow_dirs)
                if not templates:
                    templates = self._load_servant_templates(svt_id, self.face_dirs)
                if not templates:
                    raise RuntimeError(f"resource_missing: svtId={svt_id}")
                self.local_templates[svt_id] = templates
            if item["kind"] == "SUPPORT" and isinstance(svt_id, int):
                # 助战身份只用于最终日志，缺资源不影响助战位置校验。
                templates = self._load_servant_templates(svt_id, self.narrow_dirs)
                if templates:
                    self.support_templates[svt_id] = templates
        self.support_marker = self._load_named_template("battle/助战标记.png")
        if self.support_marker is None:
            raise RuntimeError("resource_missing: battle/助战标记.png")
        self.edit_marker = self._load_named_template("battle/编队决定.png")
        if self.edit_marker is None:
            raise RuntimeError("resource_missing: battle/编队决定.png")
        self.formation_confirm_marker = self._load_named_template("决定.png")
        if self.formation_confirm_marker is None:
            raise RuntimeError("resource_missing: 决定.png")

    def _match_template(self, image, template, roi=None):
        if image is None or template is None:
            return None
        scaled = template
        width = max(1, int(round(template.shape[1] * self.sx)))
        height = max(1, int(round(template.shape[0] * self.sy)))
        if width != template.shape[1] or height != template.shape[0]:
            scaled = cv2.resize(template, (width, height))
        region, offset_x, offset_y = image, 0, 0
        if roi is not None:
            x, y, roi_width, roi_height = self._scale_roi(roi)
            x, y = max(0, x), max(0, y)
            roi_width = min(roi_width, image.shape[1] - x)
            roi_height = min(roi_height, image.shape[0] - y)
            if roi_width <= 0 or roi_height <= 0:
                return None
            region = image[y:y + roi_height, x:x + roi_width]
            offset_x, offset_y = x, y
        if region.shape[0] < scaled.shape[0] or region.shape[1] < scaled.shape[1]:
            return None
        _min_value, score, _min_loc, max_loc = cv2.minMaxLoc(
            cv2.matchTemplate(region, scaled, cv2.TM_CCOEFF_NORMED)
        )
        center = (offset_x + max_loc[0] + scaled.shape[1] // 2,
                  offset_y + max_loc[1] + scaled.shape[0] // 2)
        return float(score), center

    def _match_servant(self, image, templates, roi):
        best = None
        for name, template in templates:
            result = self._match_template(image, template, roi)
            if result is not None and (best is None or result[0] > best[0]):
                best = (result[0], result[1], name)
        return best

    # ---------- 编队页识别、重排 ----------

    def _in_formation_edit(self):
        image = self._shot()
        result = self._match_template(image, self.edit_marker)
        return result is not None and result[0] >= 0.75

    def _detect_slots(self):
        image = self._shot()
        if image is None:
            self._fail("slot_unknown: 无法获取截图")
            return None
        detected = []
        for index, roi in enumerate(SLOT_ROIS):
            if self._is_empty_slot(image, roi):
                detected.append({"kind": "EMPTY", "svt_id": None, "score": 1.0})
                continue
            support = self._match_template(image, self.support_marker, roi)
            if support is not None and support[0] >= SUPPORT_THRESHOLD:
                detected.append({"kind": "SUPPORT", "svt_id": None, "score": support[0]})
                continue
            best = None
            for svt_id, templates in self.local_templates.items():
                match = self._match_servant(image, templates, roi)
                if match is not None and (best is None or match[0] > best[0]):
                    best = (match[0], svt_id, match[2])
            if best is not None and best[0] >= FACE_THRESHOLD:
                detected.append({"kind": "LOCAL", "svt_id": best[1], "score": best[0], "template": best[2]})
            else:
                # 不在 Chaldea 目标集合内的本地从者只需标为 OTHER，后续替换即可。
                detected.append({"kind": "OTHER", "svt_id": None, "score": best[0] if best else 0.0})
        return detected

    def _is_empty_slot(self, image, roi):
        """识别编队中灰色的 SELECT 空槽；不依赖文字 OCR。"""
        x, y, width, height = self._scale_roi(roi)
        region = image[y:y + height, x:x + width]
        if region.size == 0:
            return False
        channel_means = np.mean(region, axis=(0, 1))
        channel_std = float(np.mean(np.std(region, axis=(0, 1))))
        channel_delta = float(np.max(channel_means) - np.min(channel_means))
        return (
            channel_std <= EMPTY_SLOT_STD_THRESHOLD
            and channel_delta <= EMPTY_SLOT_CHANNEL_DELTA_THRESHOLD
        )

    def _matches(self, expected, current):
        if expected["kind"] == "LOCAL":
            return current["kind"] == "LOCAL" and current["svt_id"] == expected["svt_id"]
        if expected["kind"] == "SUPPORT":
            return current["kind"] == "SUPPORT"
        # Chaldea 未提供从者的槽位不参与最终匹配：用户的已有编队可以保留
        # 这些位置的从者。但它们仍可能是后续重排的可移动来源，见
        # _can_move_from。
        return True

    def _first_mismatch(self, current):
        for index, (expected, actual) in enumerate(zip(self.expected, current)):
            if not self._matches(expected, actual):
                return index
        return None

    def _can_move_from(self, index, current):
        """判断当前位置的从者能否被移去满足其他目标位置。"""
        expected = self.expected[index]
        if expected["kind"] == "EMPTY":
            # 空目标位不受最终校验约束；其中若有目标从者，必须允许把它移走。
            return current["kind"] != "EMPTY"
        return not self._matches(expected, current)

    def _same_item(self, actual, expected):
        return self._matches(expected, actual)

    def _relocate_unexpected_support(self, current, expected_support_count):
        """当 Chaldea 没有助战但游戏已选助战时，将其移至未指定槽位。"""
        if expected_support_count:
            return True
        support_index = next(
            (index for index, item in enumerate(current) if item["kind"] == "SUPPORT"),
            None,
        )
        if support_index is None:
            return True
        # 助战已处于 Chaldea 没有指定从者的位置时，不需要移动。
        if self.expected[support_index]["kind"] == "EMPTY":
            mfaalog.info(f"[自动编队] 助战已在未指定槽位{support_index + 1}，无需移动")
            return True
        empty_target = next(
            (index for index, expected in enumerate(self.expected)
             if expected["kind"] == "EMPTY"),
            None,
        )
        if empty_target is None:
            return self._fail("support_relocation_failed: Chaldea 无空位，无法保留当前助战")
        mfaalog.info(
            f"[自动编队] Chaldea 未指定助战；将助战槽位{support_index + 1}"
            f"移动至未指定槽位{empty_target + 1}"
        )
        self._drag_slot(support_index, empty_target)
        time.sleep(SWAP_SETTLE_SECONDS)
        verified = self._detect_slots()
        if verified is None or verified[empty_target]["kind"] != "SUPPORT":
            return self._fail("support_relocation_failed: 助战未移动到未指定槽位")
        return True

    def _reorder_existing(self):
        for _ in range(MAX_REORDER_OPS):
            current = self._detect_slots()
            if current is None:
                return False
            target_index = next(
                (i for i, expected in enumerate(self.expected)
                 if expected["kind"] != "EMPTY"
                 and not self._matches(expected, current[i])
                 # 已满足其自身目标的从者不能被再次搬走；否则当 Chaldea
                 # 在多个槽位要求同一从者时，会在两个正确/待补位置之间来回拖拽。
                 and any(
                     self._same_item(current[j], expected)
                     and self._can_move_from(j, current[j])
                     for j in range(6) if j != i
                 )),
                None,
            )
            if target_index is None:
                return True
            source_index = next(
                j for j in range(6)
                if j != target_index
                and self._same_item(current[j], self.expected[target_index])
                and self._can_move_from(j, current[j])
            )
            mfaalog.info(
                f"[自动编队] 重排：槽位{source_index + 1} -> 槽位{target_index + 1}"
            )
            self._drag_slot(source_index, target_index)
            time.sleep(SWAP_SETTLE_SECONDS)
            verified = self._detect_slots()
            if verified is None or not self._matches(self.expected[target_index], verified[target_index]):
                return self._fail("swap_verify_failed: 拖动后目标槽位未匹配")
        return self._fail("swap_verify_failed: 重排次数达到上限")

    def _drag_slot(self, source_index, target_index):
        source_x, source_y = self._slot_center(source_index)
        target_x, target_y = self._slot_center(target_index)
        self.controller.post_swipe(source_x, source_y, target_x, target_y, SWAP_DRAG_DURATION).wait()

    # ---------- 从者选择、筛选、替换 ----------

    def _replace_local_servants(self):
        for index, expected in enumerate(self.expected):
            if expected["kind"] != "LOCAL":
                continue
            current = self._detect_slots()
            if current is None:
                return False
            if self._matches(expected, current[index]):
                continue
            servant = self._get_servant_info(expected["svt_id"])
            if servant is None:
                return self._fail(f"servant_not_found: servant_list 中没有 {expected['svt_id']}")
            mfaalog.info(f"[自动编队] 替换槽位{index + 1}为 {servant['name']}({servant['id']})")
            if not self._enter_servant_select(index):
                return self._fail(f"servant_select_failed: 槽位{index + 1}未进入从者选择界面")
            if not self._filter_servant_list(servant):
                return self._fail(f"servant_filter_failed: {servant['name']}")
            if not self._find_and_select_servant(servant):
                return self._fail(f"servant_not_found: {servant['name']}({servant['id']})")
            if not self._wait_for(self._in_formation_edit, 5.0):
                return self._fail("servant_select_failed: 选择从者后未返回编队编辑页")
            verified, current = self._wait_for_servant_replace_verify(index, expected)
            if not verified:
                actual = current[index] if current is not None else {}
                mfaalog.error(
                    f"[自动编队] 槽位{index + 1}换人复核失败："
                    f"识别={actual.get('kind')} id={actual.get('svt_id')} "
                    f"score={actual.get('score', 0.0):.3f} "
                    f"template={actual.get('template', '-') }"
                )
                return self._fail(f"servant_replace_verify_failed: 槽位{index + 1}")
        return True

    def _wait_for_servant_replace_verify(self, index, expected):
        """等待从者卡资源加载完成，再判断本次换人是否生效。"""
        deadline = time.monotonic() + SERVANT_REPLACE_VERIFY_TIMEOUT_SECONDS
        latest = None
        while time.monotonic() < deadline:
            if self.context.tasker.stopping:
                return False, latest
            latest = self._detect_slots()
            if latest is not None and self._matches(expected, latest[index]):
                mfaalog.info(f"[自动编队] 槽位{index + 1}换人复核通过")
                return True, latest
            time.sleep(SERVANT_REPLACE_VERIFY_INTERVAL_SECONDS)
        mfaalog.warning(
            f"[自动编队] 槽位{index + 1}换人复核等待"
            f"{SERVANT_REPLACE_VERIFY_TIMEOUT_SECONDS:.0f}秒后仍未匹配"
        )
        return False, latest

    def _enter_servant_select(self, slot_index):
        for _ in range(3):
            if self._in_servant_select():
                return self._run_pipeline("自动编队-确认从者选择界面")
            x, y = self._slot_center(slot_index)
            self.controller.post_click(x, y).wait()
            time.sleep(1.0)
        if not self._in_servant_select():
            return False
        return self._run_pipeline("自动编队-确认从者选择界面")

    def _in_servant_select(self):
        """判断是否已从编队编辑页进入“从者选择”页。

        编队选择页的标题与强化从者页不同，不能复用“所持サーヴァント”
        模板。该页面右上角固定有蓝色“フィルター”按钮，因此结合“已离开
        编队编辑页”与该蓝色区域判定，避免第二次点击落在从者卡上。
        """
        image = self._shot()
        if image is None:
            return False
        edit = self._match_template(image, self.edit_marker)
        if edit is not None and edit[0] >= 0.75:
            return False
        x, y, width, height = self._scale_roi(SERVANT_FILTER_BUTTON_ROI)
        region = image[y:y + height, x:x + width]
        if region.size == 0:
            return False
        hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
        blue = cv2.inRange(hsv, (85, 80, 80), (130, 255, 255))
        ratio = float(np.count_nonzero(blue)) / blue.size
        return ratio >= SERVANT_FILTER_BLUE_RATIO

    def _filter_servant_list(self, servant):
        class_name = CLASS_TEMPLATE.get(servant.get("class"))
        if not class_name:
            return self._fail(f"servant_filter_failed: 未知职阶 {servant.get('class')}")
        override = {
            "自动编队-筛选职介": {
                "recognition": {"param": {"template": f"强化从者/职介-{class_name}.png"}}
            }
        }
        rarity = int(servant.get("rarity", 0))
        if 1 <= rarity <= 5:
            override["自动编队-筛选星级"] = {
                "recognition": {"param": {"template": f"整理礼物盒/{rarity}星未选中.png"}}
            }
        else:
            # 0 星从者没有对应筛选按钮，只按职阶筛选。
            override["自动编队-筛选星级"] = {"recognition": "DirectHit"}
        self.context.override_pipeline(override)
        if not self._run_pipeline("自动编队-筛选准备"):
            return False
        if not self.list_view_prepared:
            if not self._run_pipeline("自动编队-准备从者列表"):
                return False
            self.list_view_prepared = True
        return True

    def _find_and_select_servant(self, servant):
        templates = self._load_servant_templates(servant["id"], self.face_dirs)
        if not templates:
            templates = self._load_servant_templates(servant["id"], self.narrow_dirs)
        if not templates:
            return self._fail(f"resource_missing: 从者选择图 {servant['id']}")
        for round_index in range(MAX_FIND_SERVANT_ROUNDS):
            if self.context.tasker.stopping:
                return False
            image = self._shot()
            match = self._match_servant(image, templates, None)
            if match is not None:
                mfaalog.info(
                    f"[自动编队] 查找 {servant['name']} 第{round_index + 1}轮，"
                    f"最高分={match[0]:.3f} 模板={match[2]}"
                )
            if match is not None and match[0] >= FACE_THRESHOLD:
                self.controller.post_click(*match[1]).wait()
                if self._wait_for(self._in_formation_edit, 5.0):
                    return True
            self.controller.post_swipe(
                int(round(SWIPE_LIST_BEGIN[0] * self.sx)), int(round(SWIPE_LIST_BEGIN[1] * self.sy)),
                int(round(SWIPE_LIST_END[0] * self.sx)), int(round(SWIPE_LIST_END[1] * self.sy)), 400,
            ).wait()
            time.sleep(0.8)
        return False

    def _get_servant_info(self, svt_id):
        path = os.path.join(_CUSTOM_DIR, "servant_list.json")
        try:
            with open(path, encoding="utf-8") as file:
                servants = json.load(file).get("servants", [])
        except Exception as exc:
            mfaalog.error(f"[自动编队] 读取 servant_list.json 失败: {exc}")
            return None
        return next((item for item in servants if str(item.get("id")) == str(svt_id)), None)

    # ---------- 结束校验、日志 ----------

    def _log_support_identity_if_possible(self, current):
        image = self._shot()
        for index, expected in enumerate(self.expected):
            if expected["kind"] != "SUPPORT" or current[index]["kind"] != "SUPPORT":
                continue
            templates = self.support_templates.get(expected["svt_id"], [])
            if not templates:
                mfaalog.info(
                    f"[自动编队] 助战槽位{index + 1}位置正确；无目标头像资源，未校验助战人物"
                )
                continue
            match = self._match_servant(image, templates, SLOT_ROIS[index])
            if match is None or match[0] < FACE_THRESHOLD:
                mfaalog.warning(
                    f"[自动编队] 助战槽位{index + 1}位置正确，但人物可能与 Chaldea "
                    f"svtId={expected['svt_id']} 不一致（不阻断编队）"
                )
            else:
                mfaalog.info(f"[自动编队] 助战槽位{index + 1}人物与 Chaldea 一致")

    def _confirm_formation_change_if_present(self):
        """点击“编队决定”后，按需确认游戏的二次确认弹窗。"""
        # 明确等待弹窗动画完成；不要依赖 pipeline 的 post_delay，以免该节点被
        # 后续配置调整后导致确认截图过早。
        time.sleep(FORMATION_CONFIRM_DELAY_SECONDS)
        image = self._shot()
        result = self._match_template(image, self.formation_confirm_marker, FORMATION_CONFIRM_ROI)
        if result is None or result[0] < 0.80:
            mfaalog.info("[自动编队] 编队决定后未出现二次确认弹窗")
            return
        mfaalog.info(f"[自动编队] 命中编队二次确认决定，分数={result[0]:.3f}")
        self.controller.post_click(*result[1]).wait()
        time.sleep(FORMATION_CONFIRM_DELAY_SECONDS)

    def _log_layout(self, title, current):
        def describe(item):
            if item["kind"] == "LOCAL":
                return f"LOCAL({item['svt_id']})"
            return item["kind"]
        mfaalog.info(f"[自动编队] {title}：" + ", ".join(describe(item) for item in current))
        mfaalog.info(
            "[自动编队] 目标：" + ", ".join(
                f"LOCAL({item['svt_id']})" if item["kind"] == "LOCAL" else item["kind"]
                for item in self.expected
            )
        )

    def _run_pipeline(self, name):
        try:
            detail = self.context.run_task(name)
        except Exception as exc:
            mfaalog.error(f"[自动编队] pipeline {name} 异常: {exc}")
            return False
        if self.context.tasker.stopping:
            return False
        if detail is None or detail.status.failed or not detail.status.succeeded:
            mfaalog.error(f"[自动编队] pipeline {name} 失败")
            return False
        return True

    def _wait_for(self, predicate, timeout_seconds):
        end = time.monotonic() + timeout_seconds
        while time.monotonic() < end:
            if self.context.tasker.stopping:
                return False
            if predicate():
                return True
            time.sleep(0.4)
        return False

    def _fail(self, message):
        mfaalog.error(f"[自动编队] {message}")
        return False
