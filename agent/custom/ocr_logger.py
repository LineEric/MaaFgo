# -*- coding: utf-8 -*-
"""
全局 OCR 识别结果打印 —— 通过 context_sink 监听所有 pipeline 节点的识别事件，
对 OCR 类型的识别自动打印识别到的文本（精简版，一次接入全局生效，无需改 pipeline）。

打印策略（避免刷屏）：
- 命中(hit)：打印 best 那条文本（即匹配上的那一条）
- 未命中但识别到了文本：打印识别到的文本（最多 3 条），标注「未命中」
- 未命中且完全没识别到文本：静默，不打印

只处理 OCR，TemplateMatch / ColorMatch 等不打印。
要关闭：注释掉 main.py 里的 `import ocr_logger` 即可。
"""

from maa.agent.agent_server import AgentServer
from maa.context import ContextEventSink
from maa.event_sink import NotificationType

import mfaalog

_MAX_TEXTS = 3  # 未命中时最多打印的文本条数


@AgentServer.context_sink()
class OcrLogger(ContextEventSink):
    def on_node_recognition(self, context, noti_type, detail):
        # 只在识别结束（命中/未命中）时处理，跳过 Starting
        if noti_type not in (NotificationType.Succeeded, NotificationType.Failed):
            return

        try:
            reco = context.tasker.get_recognition_detail(detail.reco_id)
        except Exception:
            return
        if reco is None:
            return
        if str(reco.algorithm) != "OCR":
            return

        # 收集识别到的文本（去空、去重、保序）
        texts = []
        for r in reco.all_results:
            t = getattr(r, "text", None)
            if t and t.strip() and t not in texts:
                texts.append(t.strip())

        if noti_type == NotificationType.Succeeded:
            # 命中：打印匹配上的那条（best）
            best = reco.best_result
            best_text = getattr(best, "text", "") if best is not None else ""
            mfaalog.info(f"[OCR] {detail.name} 命中: {best_text.strip()!r}")
        else:
            # 未命中：只在识别到了文本时打印（有诊断价值），完全没识别到则静默
            if not texts:
                return
            preview = " / ".join(texts[:_MAX_TEXTS])
            if len(texts) > _MAX_TEXTS:
                preview += f" …共{len(texts)}条"
            mfaalog.info(f"[OCR] {detail.name} 未命中，识别到: {preview}")
