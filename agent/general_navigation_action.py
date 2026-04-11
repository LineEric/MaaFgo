import json
import os
import cv2
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

@AgentServer.custom_action("general_navigation")
class GeneralNavigationAction(CustomAction):
    def run(self, context: Context, _argv: CustomAction.RunArg) -> CustomAction.RunResult:
        """通用导航 Action
        
        从章节导航获取地图标识，构建关卡导航节点名称，获取目标关卡坐标，执行导航操作
        """
        try:
            # 1. 从章节导航获取地图标识
            chapter_nav_node = context.get_node_data("章节导航")
            if not chapter_nav_node:
                return CustomAction.RunResult(success=False, message="无法获取章节导航节点数据")
            
            map_node = chapter_nav_node.get("next", [])
            if not map_node:
                return CustomAction.RunResult(success=False, message="章节导航节点未指定地图标识")
            
            # 获取节点名称，如 "c冬木"
            map_node_name = map_node[0]
            
            # 移除前缀 "c" 获取实际地图名称，如 "冬木"
            map_name = map_node_name.replace("c", "") if map_node_name.startswith("c") else map_node_name
            
            # 2. 构建关卡导航节点名称
            quest_nav_node_name = f"{map_name}关卡导航"
            
            # 处理特殊字符，如 "由伽·刹多罗" -> "由伽刹多罗关卡导航"
            quest_nav_node_name = quest_nav_node_name.replace("·", "")
            
            # 获取关卡导航节点
            quest_nav_node = context.get_node_data(quest_nav_node_name)
            if not quest_nav_node:
                return CustomAction.RunResult(success=False, message=f"无法获取 {quest_nav_node_name} 节点数据")
            
            # 从关卡导航节点获取目标关卡
            target_quest = quest_nav_node.get("next", "")
            if not target_quest:
                return CustomAction.RunResult(success=False, message="关卡导航节点未指定目标关卡")
            
            # 3. 加载地图坐标映射
            try:
                map_file = os.path.join(os.path.dirname(__file__), "map_coordinates.json")
                with open(map_file, 'r', encoding='utf-8') as f:
                    coordinates_data = json.load(f)
            except Exception as e:
                return CustomAction.RunResult(success=False, message=f"加载地图坐标文件失败: {str(e)}")
            
            # 4. 获取目标关卡坐标
            quest_coordinates = coordinates_data.get("maps", {}).get(map_name, {}).get(target_quest)
            if not quest_coordinates:
                return CustomAction.RunResult(success=False, message=f"未找到 {map_name} 地图中 {target_quest} 关卡的坐标")
            
            # 5. 执行导航逻辑
            # 获取当前屏幕截图
            screen = context.controller.screenshot()
            if screen is None:
                return CustomAction.RunResult(success=False, message="无法获取屏幕截图")
            
            # 加载大地图模板
            # 尝试不同的路径，适应不同的目录结构
            map_template_path = None
            possible_paths = [
                os.path.join(os.path.dirname(__file__), "..", "assets", "resource", "image", f"{map_name}.png"),
                os.path.join(os.path.dirname(__file__), "..", "resource", "image", f"{map_name}.png")
            ]
            
            for path in possible_paths:
                if os.path.exists(path):
                    map_template_path = path
                    break
            
            if not map_template_path:
                return CustomAction.RunResult(success=False, message=f"未找到 {map_name} 地图模板")
            
            # 加载模板
            map_template = cv2.imread(map_template_path)
            
            # 裁剪屏幕中心区域（地图显示区域），与 FGO-py 保持一致
            map_region = screen[200:520, 200:1080]
            
            # 调整大小以提高匹配速度
            resized_map_region = cv2.resize(map_region, (0, 0), fx=0.3, fy=0.3, interpolation=cv2.INTER_CUBIC)
            
            # 模板匹配
            result = cv2.matchTemplate(resized_map_region, map_template, cv2.TM_SQDIFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            
            # 计算当前位置
            current_x = min_loc[0] / 0.3 + 440
            current_y = min_loc[1] / 0.3 + 160
            
            # 计算目标位置
            target_x, target_y = quest_coordinates
            
            # 计算距离和方向
            distance = ((target_x - current_x) ** 2 + (target_y - current_y) ** 2) ** 0.5
            
            # 检查目标是否在屏幕范围内（使用与截图相同的区域）
            screen_left = 200
            screen_top = 200
            screen_right = 1080
            screen_bottom = 520
            
            # 检查目标点是否在屏幕范围内
            if target_x >= screen_left and target_x <= screen_right and target_y >= screen_top and target_y <= screen_bottom:
                # 目标在屏幕范围内，直接点击
                context.controller.click((target_x, target_y))
                return CustomAction.RunResult(success=True, message=f"已点击目标关卡 {target_quest}")
            else:
                # 目标不在屏幕范围内，执行滑动
                # 计算滑动向量，确保滑动后目标进入屏幕范围
                # 计算目标点相对于屏幕中心的偏移
                screen_center_x = (screen_left + screen_right) / 2
                screen_center_y = (screen_top + screen_bottom) / 2
                
                # 计算目标点到屏幕中心的向量
                dx = target_x - screen_center_x
                dy = target_y - screen_center_y
                
                # 计算滑动距离，确保目标进入屏幕范围
                slide_distance = min(distance * 0.8, 300)  # 限制最大滑动距离
                
                # 计算滑动终点
                slide_end_x = current_x + dx * (slide_distance / distance)
                slide_end_y = current_y + dy * (slide_distance / distance)
                
                # 执行滑动
                context.controller.swipe((current_x, current_y), (slide_end_x, slide_end_y), duration=1000)
                
                return CustomAction.RunResult(success=True, message=f"已滑动向目标关卡 {target_quest}")
                
        except Exception as e:
            return CustomAction.RunResult(success=False, message=f"导航执行失败: {str(e)}")
