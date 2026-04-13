import json
import os
import time
import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

@AgentServer.custom_action("general_navigation")
class GeneralNavigationAction(CustomAction):
    def run(self, context: Context, _argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """通用导航 Action
        
        从地图坐标导航节点获取章节和关卡参数，执行地图相机定位和导航
        """
        try:
            # 1. 从地图坐标导航节点获取参数
            node_data = context.get_node_data("地图坐标导航")
            if not node_data:
                return CustomAction.RunResult(success=False, message="无法获取地图坐标导航节点数据")
            
            attach_data = node_data.get("attach", {})
            chapter_code = attach_data.get("chapter", "")
            target_quest = attach_data.get("quests", "")
            
            if not chapter_code or not target_quest:
                return CustomAction.RunResult(success=False, message=f"参数不完整: chapter={chapter_code}, quests={target_quest}")
            
            # 移除前缀 "c" 获取地图名称
            map_name = chapter_code.replace("c", "", 1) if chapter_code.startswith("c") else chapter_code
            
            # 2. 加载地图坐标映射
            try:
                map_file = os.path.join(os.path.dirname(__file__), "map_coordinates.json")
                with open(map_file, 'r', encoding='utf-8') as f:
                    coordinates_data = json.load(f)
            except Exception as e:
                return CustomAction.RunResult(success=False, message=f"加载地图坐标文件失败: {str(e)}")
            
            # 3. 获取目标关卡坐标
            quest_coordinates = coordinates_data.get("maps", {}).get(map_name, {}).get(target_quest)
            if not quest_coordinates:
                return CustomAction.RunResult(success=False, message=f"未找到 {map_name} 地图中 {target_quest} 关卡的坐标")
            
            # 4. 加载大地图模板
            map_template_path = os.path.join(
                os.path.dirname(__file__), 
                "..", "assets", "resource", "common", "image", "地图坐标导航", f"{map_name}.png"
            )
            
            if not os.path.exists(map_template_path):
                return CustomAction.RunResult(success=False, message=f"未找到地图模板: {map_name}")
            
            map_template = cv2.imread(map_template_path)
            if map_template is None:
                return CustomAction.RunResult(success=False, message=f"地图模板加载失败: {map_name}")
            
            # 5. 获取截图并定位相机位置
            screen = context.controller.screenshot()
            if screen is None:
                return CustomAction.RunResult(success=False, message="无法获取屏幕截图")
            
            # 裁剪地图区域（与 FGO-py 保持一致）
            map_region = screen[200:520, 200:1080]
            
            # 调整大小以提高匹配速度
            resized_map_region = cv2.resize(map_region, (0, 0), fx=0.3, fy=0.3, interpolation=cv2.INTER_CUBIC)
            
            # 反向模板匹配：大地图模板在小截图中找位置（与 FGO-py 一致）
            result = cv2.matchTemplate(resized_map_region, map_template, cv2.TM_SQDIFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            
            # 计算当前位置（还原到原始坐标，与 FGO-py 公式一致）
            current_x = int(min_loc[0] / 0.3 + 440)
            current_y = int(min_loc[1] / 0.3 + 160)
            
            # 6. 执行导航（改进版：循环滑动直到目标可见）
            target_x, target_y = quest_coordinates
            
            # 定义地图可视区域多边形（与 FGO-py 一致）
            poly = np.array([
                [230, 40], [230, 200], [40, 200], [40, 450],
                [150, 450], [220, 520], [630, 520], [630, 680],
                [980, 680], [980, 570], [1240, 570], [1240, 40]
            ])
            
            max_iterations = 10  # 防止无限循环
            for iteration in range(max_iterations):
                # 计算目标相对于屏幕中心的位置
                dx = target_x - current_x
                dy = target_y - current_y
                
                # 检查目标是否在可视区域内
                target_point = np.array([target_x, target_y])
                if cv2.pointPolygonTest(poly, tuple(target_point.astype(float)), False) >= 0:
                    # 目标已可见，点击
                    context.controller.click((target_x, target_y))
                    return CustomAction.RunResult(success=True, message=f"已点击: {target_quest} (迭代{iteration+1}次)")
                
                # 计算滑动向量（限制最大距离）
                distance = (dx**2 + dy**2)**0.5
                if distance == 0:
                    break
                
                scale = min(590/abs(dx) if dx != 0 else float('inf'),
                           310/abs(dy) if dy != 0 else float('inf'),
                           0.5)
                slide_dx = dx * scale
                slide_dy = dy * scale
                
                # 执行滑动（从中心向相反方向滑动）
                center_x, center_y = 640, 360
                start_x = center_x + slide_dx
                start_y = center_y + slide_dy
                end_x = center_x - slide_dx
                end_y = center_y - slide_dy
                
                context.controller.swipe((int(start_x), int(start_y)), 
                                        (int(end_x), int(end_y)), 
                                        duration=1000)
                
                # 等待滑动完成并重新定位
                time.sleep(1.5)
                
                # 重新获取截图并定位
                screen = context.controller.screenshot()
                if screen is None:
                    return CustomAction.RunResult(success=False, message="重新定位时无法获取截图")
                
                map_region = screen[200:520, 200:1080]
                resized_map_region = cv2.resize(map_region, (0, 0), fx=0.3, fy=0.3, interpolation=cv2.INTER_CUBIC)
                result = cv2.matchTemplate(resized_map_region, map_template, cv2.TM_SQDIFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
                current_x = int(min_loc[0] / 0.3 + 440)
                current_y = int(min_loc[1] / 0.3 + 160)
            
            return CustomAction.RunResult(success=False, message=f"导航超时: {target_quest}")
                
        except Exception as e:
            return CustomAction.RunResult(success=False, message=f"导航失败: {str(e)}")
