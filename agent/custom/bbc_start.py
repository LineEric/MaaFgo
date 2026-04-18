import os
import time
import socket
import struct
import subprocess
import logging
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

# 配置日志输出到文件
AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(AGENT_DIR, 'bbc_start_debug.log')

# 创建具名 logger
logger = logging.getLogger("BbcStart")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    _fh = logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8')
    _fh.setLevel(logging.DEBUG)
    _fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(_fh)


# BBC TCP 配置
BBC_TCP_HOST = "127.0.0.1"
BBC_TCP_PORT = 25001

# 固定 BBC 路径
AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BBC_PATH = os.path.join(AGENT_ROOT, '..', 'BBchannel')
BBC_EXE_PATH = os.path.join(BBC_PATH, 'dist', 'BBchannel64', 'BBchannel.exe')
BBC_EXE_PATH = os.path.abspath(BBC_EXE_PATH)


class BbcTcpClient:
    """BBC TCP 客户端"""
    
    def __init__(self):
        self.sock = None
    
    def connect(self, timeout: int = 10) -> bool:
        """连接到 BBC TCP 服务"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(timeout)
            self.sock.connect((BBC_TCP_HOST, BBC_TCP_PORT))
            print(f"[TCP] 已连接到 BBC TCP 服务 {BBC_TCP_HOST}:{BBC_TCP_PORT}")
            return True
        except Exception as e:
            print(f"[TCP] 连接失败: {e}")
            return False
    
    def stop(self):
        """关闭连接"""
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
            self.sock = None


@AgentServer.custom_action("start_bbc")
class StartBbc(CustomAction):
    """启动BBC进程、配置连接参数并等待TCP连接"""

    def run(self, context: Context, argv: CustomAction.RunArg) -> CustomAction.RunResult:
        try:
            # 从 Context 获取节点数据
            node_data = context.get_node_data("启动bbc")
            if not node_data:
                print(f"[StartBbc] 错误：无法获取节点数据")
                return CustomAction.RunResult(success=False)
            
            attach_data = node_data.get('attach', {})
            
            # 提取连接相关参数
            connect = attach_data.get('connect', 'auto')
            mumu_path = attach_data.get('mumu_path', '')
            mumu_index = attach_data.get('mumu_index', 0)
            mumu_pkg = attach_data.get('mumu_pkg', 'com.bilibili.fatego')
            mumu_app_index = attach_data.get('mumu_app_index', 0)
            ld_path = attach_data.get('ld_path', '')
            ld_index = attach_data.get('ld_index', 0)
            manual_port = attach_data.get('manual_port', '')
            
            print(f"[StartBbc] 连接参数: connect={connect}, manual_port={manual_port}")
            print(f"[StartBbc] MuMu参数: path={mumu_path}, index={mumu_index}, pkg={mumu_pkg}, app_index={mumu_app_index}")
            print(f"[StartBbc] LD参数: path={ld_path}, index={ld_index}")
            
            # 步骤1: 启动BBC
            print("[StartBbc] 步骤1: 启动BBC...")
            
            # 检查BBC可执行文件
            if not os.path.exists(BBC_EXE_PATH):
                print(f"[StartBbc] BBC可执行文件不存在: {BBC_EXE_PATH}")
                return CustomAction.RunResult(success=False)
            
            # 启动 BBC 进程
            print("[StartBbc] 启动 BBC 进程...")
            print(f"[StartBbc] BBC 路径：{BBC_EXE_PATH}")
            logger.info(f"[StartBbc] 启动 BBC 进程，路径：{BBC_EXE_PATH}")
                        
            # 切换到 BBC 所在目录再启动
            bbc_dir = os.path.dirname(BBC_EXE_PATH)
            _is_debug = BBC_EXE_PATH.endswith('_debug.exe')
            _creation_flags = subprocess.CREATE_NEW_CONSOLE if _is_debug else 0
            proc = subprocess.Popen([BBC_EXE_PATH], cwd=bbc_dir, creationflags=_creation_flags)
            logger.info(f"[StartBbc] 已启动进程，PID: {proc.pid}")
                        
            print("[StartBbc] BBC 启动命令已发送")
            
            # 步骤2: 连接TCP
            print("[StartBbc] 步骤2: 连接 TCP 服务...")
            tcp_client = BbcTcpClient()
            
            connect_start_time = time.time()
            connect_timeout = 30
            connected = False
            
            while time.time() - connect_start_time < connect_timeout:
                if tcp_client.connect(timeout=1):
                    connected = True
                    break
                time.sleep(0.2)
            
            if not connected:
                print("[StartBbc] TCP 连接失败，超时")
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=5)
                        if proc.poll() is None:
                            proc.kill()
                except:
                    pass
                return CustomAction.RunResult(success=False)
            
            print("[StartBbc] TCP 连接成功")
            
            # 步骤2.5: 等待免责声明关闭
            print("[StartBbc] 步骤2.5: 等待免责声明处理...")
            wait_start = time.time()
            disclaimer_closed = False
            
            while time.time() - wait_start < 30:  # 最多等待30秒
                status_result = tcp_client.send_command('get_disclaimer_status', timeout=5)
                if status_result.get('success') and status_result.get('disclaimer_closed'):
                    disclaimer_closed = True
                    print("[StartBbc] 免责声明已关闭")
                    break
                time.sleep(0.5)
            
            if not disclaimer_closed:
                print("[StartBbc] 等待免责声明超时")
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=5)
                        if proc.poll() is None:
                            proc.kill()
                except:
                    pass
                tcp_client.stop()
                return CustomAction.RunResult(success=False)
            
            # 步骤3: 发送连接配置并执行连接
            print("[StartBbc] 步骤3: 执行模拟器连接...")
            connect_result = tcp_client.send_command(connect, {
                'path': mumu_path if connect == 'connect_mumu' else ld_path,
                'index': int(mumu_index) if connect == 'connect_mumu' else int(ld_index),
                'pkg': mumu_pkg if connect == 'connect_mumu' else None,
                'app_index': int(mumu_app_index) if connect == 'connect_mumu' else None,
                'ip': manual_port if connect == 'connect_adb' else None
            }, timeout=30)
            
            tcp_client.stop()
            
            if not connect_result.get('success'):
                error_msg = connect_result.get('error', '未知错误')
                print(f"[StartBbc] 连接失败: {error_msg}")
                try:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=5)
                        if proc.poll() is None:
                            proc.kill()
                except:
                    pass
                return CustomAction.RunResult(success=False)
            
            print("[StartBbc] 连接成功")
            return CustomAction.RunResult(success=True)
            
        except Exception as e:
            print(f"[StartBbc] 启动BBC出错: {e}")
            import traceback
            traceback.print_exc()
            return CustomAction.RunResult(success=False)
