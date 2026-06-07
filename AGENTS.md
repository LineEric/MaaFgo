# AGENTS.md — MaaFgo 项目开发指南

> 本文件为 AI Agent 和开发者提供项目全貌、架构约定与开发规范。
> 最后更新：2026-04-16

---

## 1. 项目概述

**MaaFgo** 是一款基于图像识别技术的 FGO（Fate/Grand Order）自动战斗工具，专为国服 B 站版本设计。由 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 驱动，通过 MWU 前端提供 Web 访问支持。

### 技术栈

| 层级 | 技术 |
|------|------|
| 框架 | MaaFramework (图像识别 + 模拟控制) |
| Agent | Python 3 (maa.agent.AgentServer) |
| 战斗引擎 | BBchannel (独立 EXE, TCP 通信) |
| 前端 | MWU (Web) / MFAAvalonia (桌面) |
| 图像处理 | OpenCV (cv2), Pillow, NumPy |
| 数据源 | Chaldea API, Atlas Academy API |

---

## 2. 项目结构

```
MaaFgo/
├── agent/                          # Python Agent 核心
│   ├── main.py                     # Agent 入口，注册 CustomAction 并启动服务
│   ├── chaldea_converter.py        # Chaldea → BBC 队伍配置转换器
│   ├── custom/                     # CustomAction 实现
│   │   ├── bbc_action.py           # BBC 战斗 Action (Avalonia 版)
│   │   ├── bbc_action-mwu.py       # BBC 战斗 Action (MWU 版，路径差异)
│   │   ├── sequential_tasks_action.py  # 顺序执行任务 Action
│   │   └── general_navigation_action.py # 通用导航 Action (CV2 地图坐标)
│   └── utils/
│       └── map_coordinates.json    # 地图坐标映射数据
│
├── assets/                         # MaaFramework 资源定义
│   ├── interface.json              # 主界面配置 (控制器/资源/任务注册)
│   ├── config/                     # 运行时配置
│   ├── i18n/                       # 国际化
│   ├── options/                    # GUI 选项定义 (下拉框/输入框等)
│   │   ├── chaldea_team.json       # Chaldea 导入选项
│   │   ├── bbc_team_config.json    # BBC 队伍选择
│   │   ├── chapter.json            # 章节选择
│   │   └── ...
│   ├── resource/                   # 图像识别资源
│   │   ├── common/                 # 通用资源 (pipeline + image + model)
│   │   │   └── pipeline/           # Pipeline JSON 定义
│   │   │       ├── 战斗流程.json
│   │   │       ├── 地图坐标导航.json
│   │   │       ├── 回主界面.json
│   │   │       └── ...
│   │   ├── cn/                     # 国服特有资源
│   │   └── jp/                     # 日服特有资源
│   └── tasks/                      # 任务入口 JSON
│       ├── 登录.json
│       ├── 日常战斗.json
│       ├── Chaldea启动.json
│       └── ...
│
├── BBchannel/                      # BBchannel 战斗引擎
│   ├── dist/BBchannel64/           # BBC 可执行文件
│   ├── settings/                   # BBC 队伍配置文件 (JSON)
│   ├── strategy/                   # BBC 战斗策略
│   ├── servant_info_CH.json        # 从者信息 (SN → 名称映射)
│   └── 启动.cmd / 启动_debug.cmd   # 启动脚本
│
├── bbcdll/                         # BBC TCP Server (Python 注入版)
│   └── bbc_tcp_server.py           # TCP 服务端，端口 25001
│
├── chaldea/                        # Chaldea App (Dart/Flutter，独立子项目)
├── deps/                           # MaaFramework 依赖库
├── install/                        # MFAAvalonia 安装包
├── tools/                          # 开发工具脚本
│   ├── configure.py
│   ├── download_deps.py
│   └── validate_schema.py
│
├── check_resource.py               # 资源校验工具
├── requirements.txt                # Python 依赖
└── package.json                    # Node 开发依赖 (prettier 等)
```

---

## 3. 架构与数据流

### 3.1 整体架构

```
用户 (MWU/Avalonia GUI)
  │
  ├── 选项配置 ──→ assets/options/*.json ──→ pipeline_override
  │
  └── 启动任务 ──→ MaaFramework Pipeline (assets/resource/*/pipeline/*.json)
                      │
                      ├── TemplateMatch / OCR 识别
                      ├── Click / Swipe 动作
                      └── CustomAction (agent/custom/*.py)
```

### 3.2 BBC 战斗流程

1. Agent 启动 BBchannel EXE 进程
2. 通过 TCP (127.0.0.1:25001) 发送队伍配置和战斗指令
3. BBC 执行战斗，通过弹窗队列反馈状态
4. Agent 监听弹窗事件，自动处理或转发给用户

## 4. 开发规范

### 4.1 Python 代码规范

- **Python 版本**: 3.x，兼容 MaaFramework Python binding
- **依赖管理**: `requirements.txt`，仅添加运行时必需依赖
- **日志规范**: 每个 Action 使用独立命名 logger，写入独立日志文件，`propagate = False` 避免污染
- **路径处理**: 始终使用 `os.path.abspath()` 处理相对路径，以 `AGENT_ROOT` 为基准
- **错误处理**: Action 的 `run()` 方法必须返回 `CustomAction.RunResult(success=True/False)`
- **类型标注**: 公共函数使用 `typing` 模块标注参数和返回值

### 4.2 Pipeline JSON 规范

- 文件位置: `assets/resource/common/pipeline/` (通用) 或 `assets/resource/{cn,jp}/pipeline/` (服特有)
- 识别类型优先级: `TemplateMatch` > `OCR` > `DirectHit`
- 节点命名: 使用中文描述性名称，如 `"作战成功"`、`"回主界面"`
- `next` 字段: 列出所有可能的后续节点，包含错误恢复路径
- `on_error`: 必须提供错误恢复节点，避免 Pipeline 卡死

### 4.3 CustomAction 规范

- 注册方式: `@AgentServer.custom_action("action_name")` 装饰器
- 命名约定: 驼峰式 (如 `ExecuteSequentialTasks`) 或下划线式 (如 `execute_bbc_task`)
- 参数获取: 通过 `context.get_node_data("节点名")` 获取 `attach` 字段
- MWU vs Avalonia 差异: BBC 路径不同，分别维护 `bbc_action.py` 和 `bbc_action-mwu.py`

### 4.4 Options 配置规范

- 文件位置: `assets/options/*.json`
- 类型: `dropdown` (下拉选择) / `input` (文本输入)
- `pipeline_override`: 通过此字段将选项值注入 Pipeline 节点的 `attach`
- 新增选项后需在 `assets/interface.json` 的 `import` 中注册

### 4.5 资源文件规范

- 模板图片: `assets/resource/{scope}/image/` 目录，PNG 格式
- 命名: 中文描述性名称，与 Pipeline 中的 `template` 字段一致
- 校验: 使用 `python check_resource.py <dir>` 验证资源完整性

---

## 5. 角色分工与 MaaFramework 工作原理

### 5.1 角色分工

本项目涉及两类不同专业背景的开发者，理解各自职责边界有助于高效协作：

| 角色 | 职责范围 | 关注点 |
|------|----------|--------|
| **前后端 Python 开发工程师** | Agent 逻辑、数据转换、API 对接、TCP 通信、业务流程编排 | Python 代码质量、数据流正确性、异常处理、API 集成 |
| **MaaFramework 工程师** | Pipeline 编写、图像识别资源制作、interface/options 配置、识别调优 | 识别率、Pipeline 状态机正确性、资源完整性、on_error 恢复 |

**协作边界**：

- Python 工程师编写 `agent/` 下的 CustomAction，通过 `context.run_task()` / `context.get_node_data()` / `context.override_next()` 与 Pipeline 交互
- MaaFramework 工程师编写 `assets/resource/*/pipeline/*.json` 和模板图片，通过 `attach` 字段向 CustomAction 传递参数
- **接口契约**：`attach` 字段是双方的数据协议，修改前需同步确认

### 5.2 MaaFramework 工作原理（Python 工程师必读）

MaaFramework 是一个基于**图像识别 + 状态机**的自动化框架，核心概念：

#### Pipeline 状态机

```
┌─────────┐    recognition    ┌─────────┐    action    ┌─────────┐
│  节点 A  │ ──────────────→ │  识别成功 │ ──────────→ │  执行动作 │
└─────────┘                  └─────────┘              └─────────┘
                                  │                        │
                                  │ 识别失败                │ 动作完成
                                  ▼                        ▼
                             on_error 节点             next 节点列表
```

- **节点 (Node)**：Pipeline JSON 中的每个顶层键就是一个节点
- **识别 (Recognition)**：决定"当前屏幕是否匹配此节点"，类型包括：
  - `TemplateMatch`：模板图片匹配（最常用，稳定可靠）
  - `OCR`：文字识别（适合动态文本）
  - `DirectHit`：无需识别，直接执行动作（用于流程控制）
- **动作 (Action)**：识别成功后执行的操作，类型包括：
  - `Click`：点击（识别到的位置或指定坐标）
  - `Swipe`：滑动
  - `Custom`：调用 Python CustomAction
  - `DoNothing`：仅做流程跳转
- **next**：动作完成后跳转到哪些节点（框架按顺序尝试识别）
- **on_error**：识别失败或执行出错时的恢复节点

#### Pipeline 与 Agent 的交互方式

Python Agent 通过以下 API 与 Pipeline 交互：

```python
# 1. 从 Pipeline 节点获取参数（读取 attach 字段）
node_data = context.get_node_data("节点名")
attach_data = node_data.get("attach", {})

# 2. 在 CustomAction 中执行子任务（触发另一个 Pipeline 节点）
result = context.run_task("子任务名")

# 3. 动态覆盖下一个节点（根据运行时条件决定流程走向）
context.override_next("当前节点", ["目标节点A"])

# 4. 获取控制器（用于直接操作设备，如截图、点击）
controller = context.tasker.controller
controller.post_click(x, y).wait()
```

#### pipeline_override 机制

GUI 选项通过 `pipeline_override` 将用户选择注入 Pipeline 节点的 `attach` 字段：

```
assets/options/bbc_team_config.json
  └── pipeline_override: { "执行BBC任务": { "attach": { "bbc_team_config": "{值}" } } }
        │
        ▼
MaaFramework 运行时合并到 Pipeline 节点
        │
        ▼
Python CustomAction 通过 context.get_node_data() 读取
```

这是 **Options → Pipeline → CustomAction** 的数据流主线，理解这条链路是协作的基础。

#### 关键约束

- Pipeline 是**声明式**的（JSON 描述"做什么"），CustomAction 是**命令式**的（Python 描述"怎么做"）
- `context.override_next()` 只能覆盖**当前节点**的 next，不能修改其他节点
- CustomAction 返回 `RunResult(success=False)` 会导致 Pipeline 进入 `on_error` 路径
- Pipeline 节点之间是**异步尝试**的：`next` 列表中的节点按顺序识别，第一个匹配的执行

---

## 7. API 与外部服务

| 服务 | URL | 用途 |
|------|-----|------|
| Chaldea API | `https://worker.chaldea.center/api/v4` | 关卡队伍排行、队伍详情 |
| Atlas Academy API | `https://api.atlasacademy.io` | 从者/礼装名称数据 (CN export) |
| BBC TCP | `127.0.0.1:25001` | 本地 BBchannel 进程通信 |

### Chaldea API 端点

- `GET /quest/{questId}/team?phase=&page=&limit=` — 关卡队伍排行
- `GET /team/{teamId}` — 单个队伍详情 (含 content 压缩数据)

### 数据解码

Chaldea `content` 字段为 gzip + base64url 编码的 JSON，前缀 `G` 表示 gzip 压缩。
解码流程: 去前缀 → base64url 解码 → gzip 解压 → JSON 解析

---

## 8. 注意事项

- ⚠️ 模拟器必须使用 ADB 方式连接，Win32 窗口方式会导致截图黑屏
- ⚠️ MWU 和 Avalonia 环境下 BBC 路径不同，注意使用对应的 Action 文件
- ⚠️ Chaldea API 的 SSL 验证已禁用 (`verify_mode = ssl.CERT_NONE`)，生产环境应考虑恢复
- ⚠️ BBC TCP 端口固定为 25001，多实例运行需注意端口冲突
- ⚠️ `agent/custom/` 下的模块会被 `main.py` 自动 import，新增文件需同步更新 import
- ⚠️ Pipeline 的 `on_error` 必须提供恢复路径，否则任务可能卡死
