"""标定脚本：喂一张选卡界面截图，打印每张卡 ROI 的卡色预测 + HSV 统计，并保存带框 overlay。

用法：
  python tools/battle_calib/calibrate.py <screenshot.png>
      [--rois tools/battle_calib/calib_rois.json] [--out overlay.png] [--pick]

流程：改 calib_rois.json 里的 card_roi → 跑本脚本 → 看 overlay 框对没对准、看预测卡色对不对
→ 反复调 ROI 和（必要时）cv_perception.HSV_BOUNDS，直到 5 张卡全判对。
--pick：打开窗口点鼠标取坐标（需图形界面；无界面环境会跳过）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cv_perception as cvp  # noqa: E402

_LABEL_COLOR = {"B": (0, 0, 255), "A": (255, 0, 0), "Q": (0, 200, 0), None: (0, 255, 255)}


def _load_rois(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def analyze(img, rois: dict, out_path: str) -> None:
    overlay = img.copy()
    print(f"图像尺寸: {img.shape[1]}x{img.shape[0]}  (期望 1280x720)")
    print(f"{'卡':<3}{'ROI':<22}{'预测':<6}{'置信':<8}{'meanHSV':<20}{'counts'}")
    for slot in ("1", "2", "3", "4", "5"):
        box = rois.get("card_roi", {}).get(slot, [0, 0, 0, 0])
        if box[2] <= 0 or box[3] <= 0:
            print(f"{slot:<3}{'未设置':<22}")
            continue
        roi = cvp.crop(img, box)
        color, conf, counts = cvp.predict_color(roi)
        h, s, v = cvp.mean_hsv(roi)
        print(f"{slot:<3}{str(box):<22}{str(color):<6}{conf:<8.2f}"
              f"{f'({h:.0f},{s:.0f},{v:.0f})':<20}{counts}")
        x, y, w, hh = box
        cv2.rectangle(overlay, (x, y), (x + w, y + hh), _LABEL_COLOR.get(color), 2)
        cv2.putText(overlay, str(color), (x, max(0, y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, _LABEL_COLOR.get(color), 2)

    # NP 卡 / 攻击钮 / 敌人点：仅画出来供核对位置
    for slot, box in rois.get("np_roi", {}).items():
        if box[2] > 0 and box[3] > 0:
            x, y, w, hh = box
            cv2.rectangle(overlay, (x, y), (x + w, y + hh), (200, 200, 0), 2)
            cv2.putText(overlay, f"NP{slot}", (x, max(0, y - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 0), 2)
    ab = rois.get("attack_btn", [0, 0])
    if ab != [0, 0]:
        cv2.drawMarker(overlay, tuple(ab), (255, 0, 255), cv2.MARKER_CROSS, 20, 2)
    for slot, pt in rois.get("enemy_point", {}).items():
        if pt != [0, 0]:
            cv2.drawMarker(overlay, tuple(pt), (0, 128, 255), cv2.MARKER_TILTED_CROSS, 18, 2)

    cv2.imwrite(out_path, overlay)
    print(f"\noverlay 已保存: {out_path}")


def pick(img) -> None:
    print("点击取坐标；按 q 退出。")
    win = "pick (q to quit)"

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            print(f"click: [{x}, {y}]")

    try:
        cv2.namedWindow(win)
        cv2.setMouseCallback(win, on_mouse)
        while True:
            cv2.imshow(win, img)
            if (cv2.waitKey(20) & 0xFF) == ord("q"):
                break
        cv2.destroyAllWindows()
    except cv2.error as e:
        print(f"无图形界面，--pick 跳过: {e}")


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("screenshot")
    ap.add_argument("--rois", default=os.path.join(here, "calib_rois.json"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--pick", action="store_true")
    args = ap.parse_args()

    img = cv2.imread(args.screenshot)
    if img is None:
        print(f"读不到图: {args.screenshot}")
        sys.exit(1)

    rois = _load_rois(args.rois)
    out = args.out or (os.path.splitext(args.screenshot)[0] + "_overlay.png")
    analyze(img, rois, out)
    if args.pick:
        pick(img)


if __name__ == "__main__":
    main()
