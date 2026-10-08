# -*- coding: utf-8 -*-
"""「一份记录 = 一个 JSON 文件」的存储基类。

proposal / artifact / chat / team_run 四个 store 原本各写一遍相同的四件事，而它们
出错的方式恰好就是最要命的那几类，所以统一到这里：

  1. **锁必须是类级，不是实例级**。服务端每个请求都新建一个 store（见
     web/state.py 的 _store / _astore / _cstore / _tstore，25 处调用点），实例级锁
     跨请求根本不共享 —— 那样加锁等于没加。TeamRunStore 早就踩过并注释了这一点，
     而 ProposalStore 上一轮补锁时用的却是实例级 RLock（无效）。用 RLock 是因为
     set_status → update → _write 是同线程嵌套。
  2. **临时文件名必须唯一**。四处原先都写死 "<id>.json.tmp"：两个线程同时写同一条
     记录时，先 replace 的会让后写的持有失效句柄，Windows 上直接
     PermissionError(WinError 5/32)（实测复现过）。原子替换语义要求每次一个新名字。
  3. **读-改-写必须整体在锁内**。单条 write 原子只保证不出半份文件，挡不住两个请求
     各自读到旧版、后写的把先写的字段整片覆盖 —— 丢更新且没有任何报错。
  4. **坏文件不能静默当"不存在"**。get 解析失败时保留返回 None（调用方都按
     "没有这条记录"处理），但计数与列举会跳过它，不会让一条坏 JSON 拖垮整个列表。
"""
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JsonRecordStore:
    # 子类可覆盖：id 清洗长度与空值兜底、保留条数（None = 不清理）
    SLUG_LEN = 60
    DEFAULT_SLUG = "local"
    KEEP: Optional[int] = None

    # 进程级共享锁，理由见模块 docstring 第 1 条
    _lock = threading.RLock()

    def __init__(self, base_dir):
        self.base = Path(base_dir).resolve()
        self.base.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------- 路径与清洗
    @classmethod
    def _slug(cls, s: str) -> str:
        return (re.sub(r"[^0-9A-Za-z._-]+", "-", str(s or cls.DEFAULT_SLUG))
                .strip("-")[:cls.SLUG_LEN] or cls.DEFAULT_SLUG)

    def _path(self, rid: str) -> Path:
        return self.base / f"{self._slug(rid)}.json"

    # ------------------------------------------------------------------ 读
    def get(self, rid: str) -> Optional[Dict]:
        path = self._path(rid)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _iter_raw(self, newest_first: bool = True) -> List[Dict]:
        """按文件名顺序读出所有记录；坏文件跳过（不让一条脏数据拖垮列表）。"""
        files = sorted(self.base.glob("*.json"), key=lambda p: p.name,
                       reverse=newest_first)
        out: List[Dict] = []
        for f in files:
            try:
                out.append(json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out

    # ------------------------------------------------------------------ 写
    def _write(self, payload: Dict) -> None:
        """原子写。不加 updated —— 那是 update() 的职责，create() 自己带好时间戳。"""
        target = self._path(payload["id"])
        tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                                      default=str), encoding="utf-8")
            tmp.replace(target)
        finally:
            if tmp.exists():          # replace 成功时它已经不在了
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def update(self, payload: Dict) -> Dict:
        """盖上 updated 再写。锁在这里，保证同一份记录的读-改-写不交叉。"""
        with self._lock:
            payload["updated"] = _iso_now()
            self._write(payload)
        return payload

    def set_status(self, rid: str, status: str, **extra) -> Optional[Dict]:
        with self._lock:              # 整个「读 → 改 → 写」在同一把锁里
            rec = self.get(rid)
            if not rec:
                return None
            rec["status"] = status
            rec.update(extra)
            return self.update(rec)

    # -------------------------------------------------------------- 保留策略
    def prune(self) -> int:
        """只保留最近 KEEP 份（按文件名序，id 里带时间戳所以等价于按时间）。

        KEEP 为 None 时什么都不做。返回清理掉的文件数。
        """
        if not self.KEEP:
            return 0
        files = sorted(self.base.glob("*.json"), key=lambda p: p.name, reverse=True)
        removed = 0
        for f in files[self.KEEP:]:
            try:
                f.unlink()
                removed += 1
            except OSError:
                continue              # 被占用就留给下次，不硬来
        return removed
