"""长任务作业的「人工介入总线」。

长任务动辄跑十几分钟到几十分钟，用户必须能在半路插手：发现某个子 Agent 方向
错了、或者想追问一句，不必等整轮跑完。

这里解决的核心问题是：模型调用是**同步且不可中断**的一次 HTTP 请求，没法规矩
地打断。所以介入采取「**安全边界消费**」模型：

- 用户随时可以把指令投进收件箱（`post`），请求立刻返回，界面不卡；
- 编排器在**每个安全边界**（每个工作项开始前、每次模型调用前）调用
  `checkpoint()`，此时才消费收件箱；
- `pause` / `stop` 这类控制指令同样只在边界生效，界面必须如实告诉用户
  「已暂停，将在当前步骤结束后生效」——而不是假装立刻停住。

边界消费是刻意的选择：任何"立刻中断"的承诺都会在真实运行里骗人。
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

# 干预类型：
#   message 追问 / 补充信息 / 纠偏（会在下一轮注入该角色的提示词）
#   replan  要求决策官带上下文重新规划（在下一个工作项边界生效）
#   skip    跳过某个工作项（item 填工作项 id，留空表示跳过当前工作项）
#   pause   暂停（在下一个安全边界生效）
#   resume  继续
#   stop    终止（在下一个安全边界生效）
KINDS = ("message", "replan", "skip", "pause", "resume", "stop")

# 投递瞬间给界面看的一句话（让用户确认"指令确实发出去了"）
_INTERVENTION_DETAIL = {
    "message": "已向「{agent}」发送人工指令，会在它下一步开始时注入",
    "replan": "已要求决策官带上下文重新规划，会在下一个工作项边界生效",
    "skip": "已要求跳过工作项 {item}，会在下一个安全边界生效",
    "pause": "已请求暂停：会在当前步骤结束后停下（模型调用无法中途打断）",
    "resume": "已请求继续运行",
    "stop": "已请求终止：会在当前步骤结束后停下，已完成的产出会保留",
}

BUSES: Dict[str, "TeamBus"] = {}
_BUSES_LOCK = threading.Lock()


def create_bus(run_id: str, pause_timeout: float = 1800.0) -> "TeamBus":
    with _BUSES_LOCK:
        bus = TeamBus(run_id, pause_timeout=pause_timeout)
        BUSES[run_id] = bus
        return bus


def get_bus(run_id: str) -> Optional["TeamBus"]:
    with _BUSES_LOCK:
        return BUSES.get(run_id)


def drop_bus(run_id: str) -> None:
    with _BUSES_LOCK:
        BUSES.pop(run_id, None)


def _emit(emit, evt: Dict) -> None:
    if emit is None:
        return
    try:
        emit(evt)
    except Exception:
        pass


class TeamBus:
    def __init__(self, run_id: str, pause_timeout: float = 1800.0):
        self.run_id = run_id
        self.pause_timeout = float(pause_timeout or 0)
        self._lock = threading.Lock()
        self._inbox: List[Dict] = []
        self._emitter = None
        self._seq = 0
        # _run 被 set = 运行中；clear = 已暂停（在下一个边界等待）
        self._run = threading.Event()
        self._run.set()
        self._stop = threading.Event()
        self._pause_started: Optional[float] = None
        self.paused = False
        self.stopped = False

    def set_emitter(self, fn) -> None:
        """编排器把事件出口挂上来，好让「人工投递」也能实时广播到运行视图——
        投递方是 HTTP 线程，不挂这个出口的话用户点完要等下一个边界才看到反应。"""
        self._emitter = fn

    # ------------------------------------------------------------- 投递
    def post(self, kind: str, agent: str = "*", text: str = "",
             item: str = "") -> Dict:
        """投递一条干预。返回落库用的记录（调用方负责持久化与广播）。"""
        kind = (kind or "message").strip().lower()
        if kind not in KINDS:
            raise ValueError(f"不支持的干预类型: {kind}")
        agent = (agent or "*").strip() or "*"
        text = str(text or "").strip()
        if kind == "message" and not text:
            raise ValueError("追问/纠偏内容不能为空")
        with self._lock:
            self._seq += 1
            rec = {
                "seq": self._seq,
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "kind": kind,
                "agent": agent,
                "text": text,
                "item": str(item or "").strip(),
                "consumed_by": "",
                "consumed_at": "",
            }
            if kind == "pause":
                self._run.clear()
                self.paused = True
                self._pause_started = None
            elif kind == "resume":
                self.paused = False
                self._run.set()
            elif kind == "stop":
                self.stopped = True
                self._stop.set()
                self._run.set()   # 解除暂停，好让边界能看见 stop
            self._inbox.append(rec)
        _emit(self._emitter, {
            "type": "intervention", "seq": rec["seq"], "kind": rec["kind"],
            "agent": rec["agent"], "text": rec["text"], "item": rec["item"],
            "ts": rec["ts"],
            "detail": _INTERVENTION_DETAIL.get(rec["kind"], "").format(
                agent=rec["agent"], item=rec["item"] or "当前工作项"),
        })
        return rec

    def snapshot(self) -> Dict:
        with self._lock:
            return {"paused": self.paused, "stopped": self.stopped,
                    "pending": [dict(x) for x in self._inbox
                                if not x["consumed_at"]]}

    # ------------------------------------------------------------- 消费
    def checkpoint(self, agent_id: str, emit=None) -> Dict:
        """安全边界。先等暂停解除，再取走发给该角色的指令。

        返回 {"stop", "notes", "replan", "skip", "records"}：
        - notes   注入提示词的人工指令文本
        - replan  是否要求重规划
        - skip    要跳过的工作项 id（"*" 表示当前工作项）
        """
        notified = False
        while not self._run.wait(0.5):
            if self._stop.is_set() or self.stopped:
                break
            if self._pause_started is None:
                self._pause_started = time.time()
            if not notified:
                notified = True
                _emit(emit, {
                    "type": "run_status", "status": "paused",
                    "detail": "已暂停：会在当前步骤结束后停下。现在可以追问、纠偏，"
                              "或者点「继续」恢复。",
                })
            if self.pause_timeout and time.time() - self._pause_started > self.pause_timeout:
                self.paused = False
                self._run.set()
                _emit(emit, {
                    "type": "run_status", "status": "running",
                    "detail": f"暂停已超过 {int(self.pause_timeout // 60)} 分钟无人应答，"
                              "自动继续运行（你随时可以再次暂停）。",
                })
                break
        self._pause_started = None
        out = self.take(agent_id)
        if self._stop.is_set() or self.stopped:
            out["stop"] = True
        return out

    def take(self, agent_id: str) -> Dict:
        """取走发给 agent_id（或 * ）的干预，标记为已消费。"""
        out = {"stop": False, "notes": [], "replan": False, "skip": [],
               "records": []}
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            keep: List[Dict] = []
            for rec in self._inbox:
                if rec["consumed_at"]:
                    continue
                if rec["agent"] not in ("*", agent_id, "all"):
                    keep.append(rec)
                    continue
                if rec["kind"] in ("pause", "resume"):
                    # 控制类指令在 post 时已经生效，这里只做记录，不做重复动作
                    rec["consumed_by"], rec["consumed_at"] = agent_id, now
                    out["records"].append(dict(rec))
                    continue
                rec["consumed_by"], rec["consumed_at"] = agent_id, now
                out["records"].append(dict(rec))
                if rec["kind"] == "message":
                    out["notes"].append(rec["text"])
                elif rec["kind"] == "replan":
                    out["replan"] = True
                    if rec["text"]:
                        out["notes"].append(rec["text"])
                elif rec["kind"] == "skip":
                    # 留空 = 跳过当前正在做的工作项，用 @current 与写死的 id 区分
                    out["skip"].append(rec["item"] or "@current")
                elif rec["kind"] == "stop":
                    out["stop"] = True
            self._inbox = keep
        return out

    def request_stop(self) -> None:
        self.stopped = True
        self._stop.set()
        self._run.set()
