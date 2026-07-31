"""路 A 黄金集验证：对 fixtures 目录里的"截图 + 标注"跑离线卡色预测，报准确率（无需设备）。

用法：
  python tools/battle_calib/verify_perception.py <fixtures_dir>
      [--rois tools/battle_calib/calib_rois.json]

fixtures_dir 里成对存放：  xxx.png  +  xxx.json（标注，Tier-0 格式）
标注示例见 docs/zh_cn/自动战斗_标定与验证操作步骤.md。

当前实现：验证**卡色**（纯 HSV，路 A）。scene / np_cards / enemies 需模板或真机，
标注里有也先跳过并计入"未验证"，留待路 B（Tasker.post_recognition）或真机。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv_perception as cvp  # noqa: E402


def verify_one(img, ann: dict):
    """返回 (对, 总, [失配描述])。仅验证选卡界面的卡色。"""
    if ann.get("scene") != "command_selection":
        return 0, 0, []
    ok = total = 0
    misses = []
    rois = ann.get("_card_roi_override")  # 允许标注内联 ROI；否则用全局 rois（见 main）
    for card in ann.get("cards", []):
        slot = str(card["ui_slot"])
        expected = card["color"]
        box = (rois or _GLOBAL_ROIS.get("card_roi", {})).get(slot, [0, 0, 0, 0])
        if box[2] <= 0 or box[3] <= 0:
            misses.append(f"卡{slot}: ROI 未设置")
            total += 1
            continue
        pred, conf, _ = cvp.predict_color(cvp.crop(img, box))
        total += 1
        if pred == expected:
            ok += 1
        else:
            misses.append(f"卡{slot}: 期望 {expected} 得到 {pred} (conf {conf:.2f})")
    return ok, total, misses


_GLOBAL_ROIS: dict = {}


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("fixtures_dir")
    ap.add_argument("--rois", default=os.path.join(here, "calib_rois.json"))
    args = ap.parse_args()

    global _GLOBAL_ROIS
    with open(args.rois, "r", encoding="utf-8") as f:
        _GLOBAL_ROIS = json.load(f)

    anns = sorted(glob.glob(os.path.join(args.fixtures_dir, "*.json")))
    if not anns:
        print(f"没找到标注 json: {args.fixtures_dir}")
        sys.exit(1)

    tot_ok = tot_all = 0
    for ann_path in anns:
        with open(ann_path, "r", encoding="utf-8") as f:
            ann = json.load(f)
        img_path = os.path.join(args.fixtures_dir, ann.get("screenshot", ""))
        if not os.path.exists(img_path):
            img_path = os.path.splitext(ann_path)[0] + ".png"
        img = cv2.imread(img_path)
        if img is None:
            print(f"[跳过] 读不到图: {img_path}")
            continue
        ok, total, misses = verify_one(img, ann)
        tot_ok += ok
        tot_all += total
        tag = "OK" if not misses else "FAIL"
        print(f"[{tag}] {os.path.basename(ann_path)}  卡色 {ok}/{total}")
        for m in misses:
            print(f"       - {m}")

    if tot_all:
        print(f"\n卡色总准确率: {tot_ok}/{tot_all} = {tot_ok / tot_all:.1%}  (门槛 ≥99%)")


if __name__ == "__main__":
    main()
