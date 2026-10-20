"""仓库技术栈自动识别（需求开发模块专用）。

为什么要有这一层：旧版「技术栈」是用户在表单里**手选**的 6 个写死枚举，不选就
默认 React + TypeScript —— 用户配置了仓库，AI 却不去读它，这是「死板」的头号
来源。现在 ``run_req`` 在运行前调 ``detect_stack(repo_root)``：读依赖清单
（package.json / tsconfig.json / manifest.json / requirements.txt / pom.xml /
go.mod …），推断技术栈与构建信息，直接喂进分诊/定位/实施的提示词。

口径：
- ``framework`` 是给提示词的一句话标签（"Vue 3 + TypeScript"），识别不出就留空
  —— 由调用方写「以仓库真实代码为准」，**绝不猜**（猜错比不猜更害人：模型会照着
  React 的写法改 Vue 仓库）。
- ``summary`` 是给人与模型看的依据说明（构建工具 / 包管理 / 组件库 / 后端信号），
  让「为什么判定成这个栈」可解释、可纠正（用户仍可在高级选项里粘贴代码参考覆盖）。
- 只读不写：全部是 open + 少量 glob，无子进程，毫秒级。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

# 单仓最多读多少份 package.json（monorepo packages/* 一层；够判栈了，别扫深）
_MAX_PKG_FILES = 40


def _read_json(path: Path) -> Dict:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _deps_of(pkg: Dict) -> Dict[str, str]:
    deps: Dict[str, str] = {}
    for key in ("dependencies", "devDependencies"):
        v = pkg.get(key)
        if isinstance(v, dict):
            for k, ver in v.items():
                deps[str(k)] = str(ver or "")
    return deps


def _version_major(ver: str) -> str:
    m = ""
    for ch in ver.strip().lstrip("^~>=< "):
        if ch.isdigit():
            m += ch
        else:
            break
    return m


def detect_stack(repo_root: str) -> Dict:
    """读仓库依赖清单，返回 {"framework": str, "summary": str, "sources": [...]}。

    framework 为空串 = 没认出来（调用方必须显式处理，不能兜底成某个默认栈）。
    """
    root = Path(str(repo_root or "").strip())
    out = {"framework": "", "summary": "", "sources": []}
    if not root or not root.exists():
        return out

    sources: List[str] = []
    deps: Dict[str, str] = {}
    # 根 package.json 优先，monorepo 再扫 packages/* 一层
    for pkg_path in [root / "package.json", *sorted(root.glob("packages/*/package.json"))][:_MAX_PKG_FILES]:
        if not pkg_path.is_file():
            continue
        pkg = _read_json(pkg_path)
        if not pkg:
            continue
        rel = str(pkg_path.relative_to(root)).replace("\\", "/")
        sources.append(rel)
        for k, v in _deps_of(pkg).items():
            deps.setdefault(k, v)

    def has(name: str) -> bool:
        return name in deps

    notes: List[str] = []
    base = ""
    if has("@dcloudio/uni-app") or ((root / "src" / "manifest.json").is_file()
                                    and (root / "src" / "pages.json").is_file()):
        base = "uni-app"
    elif (root / "app.json").is_file() and (root / "project.config.json").is_file() \
            and not has("vue") and not has("react"):
        base = "小程序原生"
    elif has("vue"):
        # Vue 2 / Vue 3 的写法差异是决定性的，版本号必须进标签
        base = f"Vue {_version_major(deps['vue']) or '?'}"
    elif has("react"):
        # React 沿用既有 FRAMEWORKS 命名（不带版本号），避免提示词口径漂移
        base = "React"

    if base and base != "uni-app" and base != "小程序原生":
        ts = has("typescript") or (root / "tsconfig.json").is_file()
        if ts and (root / "tsconfig.json").is_file() and "tsconfig.json" not in sources:
            sources.append("tsconfig.json")
        base += " + TypeScript" if ts else " + JavaScript"

    # 后端 / 其它语言信号：不冒充前端栈，但要让模型知道这是个什么仓
    backend = []
    if (root / "requirements.txt").is_file() or (root / "pyproject.toml").is_file():
        backend.append("Python")
        sources.append("requirements.txt" if (root / "requirements.txt").is_file()
                       else "pyproject.toml")
    if (root / "pom.xml").is_file():
        backend.append("Java(Maven)")
        sources.append("pom.xml")
    if (root / "go.mod").is_file():
        backend.append("Go")
        sources.append("go.mod")
    if (root / "Cargo.toml").is_file():
        backend.append("Rust")
        sources.append("Cargo.toml")

    if (root / "vite.config.ts").is_file() or (root / "vite.config.js").is_file() or has("vite"):
        notes.append("构建 vite")
    elif has("webpack") or (root / "vue.config.js").is_file():
        notes.append("构建 webpack")
    for mgr, lock in (("pnpm", "pnpm-lock.yaml"), ("yarn", "yarn.lock"),
                      ("npm", "package-lock.json")):
        if (root / lock).is_file():
            notes.append(f"包管理 {mgr}")
            sources.append(lock)
            break
    ui_libs = [lib for lib in ("element-plus", "element-ui", "ant-design-vue", "antd",
                               "arco-design/web-vue", "naive-ui", "vant")
               if has(lib)]
    if ui_libs:
        notes.append("组件库 " + "/".join(ui_libs[:3]))
    if backend:
        notes.append("后端 " + "/".join(backend))

    out["framework"] = base
    out["summary"] = "；".join(notes)
    out["sources"] = sources[:10]
    return out
