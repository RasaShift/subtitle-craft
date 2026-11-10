#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-10（新工具：还原前自动备份，防 git checkout 丢未提交改动——台账 #54）
"""restore.py -- 安全还原：先把要还原的文件备份走，再让 git 还原。

为什么有它：11-09 踩坑——用 `git checkout` 还原测试数据，把未提交的修复一起
回滚，丢了什么事后都说不清。checkout / restore 的破坏是静默的，没有任何东西
会喊。直接拦做不到（git 没有 pre-checkout 钩子；shell 包装要动个人机器配置），
所以让还原天生带备份：备份留在 `<仓库根>/_tmp/restore_backup/<时间戳>/`，
错了能撤。**还原一律用它，别裸敲 `git checkout -- <path>` / `git restore`。**

用法：
    python tools/restore.py <path>...     # 路径相对当前目录；只动 git 跟踪的文件

行为：
  - tracked 文件：逐个备份（字节级自验）→ `git checkout -- <path>...`
  - git 不跟踪的路径：不动、不还原，警告一声（checkout 本来也碰不到它）
  - 备份保留相对路径结构；同秒重跑自动加 `_2` 后缀，不覆盖旧备份
  - 只管工作区还原；`git reset --hard`、分支切换不在范围
"""
import subprocess
import shutil
import sys
from datetime import datetime
from pathlib import Path


def git(*args, cwd=None):
    return subprocess.run(['git', *args], capture_output=True, text=True, cwd=cwd)


def main(argv):
    if not argv:
        print(__doc__.strip())
        return 2
    cwd = Path.cwd()
    top = git('rev-parse', '--show-toplevel')
    if top.returncode != 0:
        print('ERROR：当前目录不在 git 仓库里，没有可还原的基线。')
        return 1
    root = Path(top.stdout.strip())

    # 分类：git ls-files 只返回 tracked 文件，untracked 在这里就被挡下
    files = []            # 将被还原的 tracked 文件（相对 cwd）
    skipped = []
    for p in argv:
        lr = git('ls-files', '-z', '--', p, cwd=str(cwd))
        if lr.returncode != 0:
            print(f'ERROR pathspec 无效：{p}（{lr.stderr.strip()}）')
            return 1
        hits = [line for line in lr.stdout.split('\0') if line]
        if hits:
            files.extend(hits)
        else:
            skipped.append(p)
    for s in skipped:
        print(f'WARN 跳过（git 不跟踪，还原不碰它）：{s}')
    if not files:
        print('没有 tracked 文件可还原。')
        return 0

    # 备份：同秒重跑加 _2 后缀，绝不覆盖旧备份
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    bdir = root / '_tmp' / 'restore_backup' / stamp
    n = 2
    while bdir.exists():
        bdir = root / '_tmp' / 'restore_backup' / f'{stamp}_{n}'
        n += 1
    backed = 0
    for rel in files:
        src = cwd / rel
        if not src.is_file():
            continue    # 工作区已删除的文件：checkout 会把它从 index 找回，是恢复不是丢失
        dst = bdir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        assert dst.is_file() and dst.stat().st_size == src.stat().st_size, f'备份自验失败：{rel}'
        backed += 1

    # 还原
    r = git('checkout', '--', *files, cwd=str(cwd))
    if r.returncode != 0:
        print(f'ERROR 还原失败（备份还在 {bdir}，无损失）：\n{r.stderr.strip()}')
        return 1
    print(f'备份 {backed} 个文件 → {bdir}')
    print(f'已还原 {len(files)} 个 tracked 文件（还原到 index 版本）。')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
