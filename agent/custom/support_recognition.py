"""MaaFramework 助战列表行锚点 Custom Recognition。"""
from __future__ import annotations

import json
import os
import sys

_AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from maa.agent.agent_server import AgentServer
from maa.custom_recognition import CustomRecognition

from support_selection.row_layout import build_visible_rows


@AgentServer.custom_recognition("support_row_anchor")
class SupportRowAnchorRecognition(CustomRecognition):
    """返回当前屏幕第 N 条完整助战记录的确认按钮位置。

    这是第一阶段调试入口。后续 `support_candidate` 会复用同一行布局，
    再增加从者身份、等级与宝具等级判断。
    """

    def analyze(self, context, argv):
        param = _parse_param(argv.custom_recognition_param)
        row_index = param.get("row_index", 0)
        if isinstance(row_index, bool) or not isinstance(row_index, int) or row_index < 0:
            return CustomRecognition.AnalyzeResult(
                None,
                {"error": "row_index must be a non-negative integer"},
            )

        detail = context.run_recognition(
            "助战选择-确认按钮锚点",
            argv.image,
        )
        if detail is None or not detail.hit:
            return CustomRecognition.AnalyzeResult(
                None,
                {"error": "confirm_button_anchor_not_found", "rows": []},
            )

        results = detail.filtered_results or detail.all_results
        rows = build_visible_rows(
            (result.box for result in results),
            frame_width=int(argv.image.shape[1]),
            frame_height=int(argv.image.shape[0]),
        )
        row_details = [
            {
                "index": row.index,
                "confirm_button": row.confirm_button.as_tuple(),
                "row_box": row.row_box.as_tuple(),
                "level_roi": row.level_roi.as_tuple(),
                "name_roi": row.name_roi.as_tuple(),
                "np_roi": row.np_roi.as_tuple(),
            }
            for row in rows
        ]
        if row_index >= len(rows):
            return CustomRecognition.AnalyzeResult(
                None,
                {
                    "error": "requested_row_not_visible",
                    "requested_row": row_index,
                    "rows": row_details,
                },
            )

        selected = rows[row_index]
        return CustomRecognition.AnalyzeResult(
            selected.confirm_button.as_tuple(),
            {
                "selected_row": row_index,
                "rows": row_details,
            },
        )


def _parse_param(raw: str) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}