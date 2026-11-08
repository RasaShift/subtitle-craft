#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-08（新建：进度台账生成器；判「文件名能证明的进度」，判不出来的标损）
"""gen_progress.py -- 进度台账生成器：状态只来自磁盘，不来自回忆

为什么有它（2026-11-08，剧库 #8 实测）：台账原来靠人抄磁盘，一天内落后三次——
某集换底本重做已完工，台账那行还写「作废待重跑」；同一个「对稿候选数」在库里
同时存在三个值。人抄一次就多一份与磁盘不同的副本，而**落后不会报错**，只会让
人按旧状态排活，比报错更贵。

判据只有一句：**进度 = 文件名能证明的最后一道工序**。判不出来的标损，不许猜。
对稿 / 定档 / 审稿的痕迹不在这套命名里，本表不管——它们的真身在各自产物。

用法（在剧库根执行，工具在通用库）：
    python <库>/tools/gen_progress.py 03_工作区 --final 05_成品 --out 06_治理/进度台账.md
    python <库>/tools/gen_progress.py 03_工作区 --stdout
    python <库>/tools/gen_progress.py 03_工作区 --out 06_治理/进度台账.md --check

退出码：0 正常；1 --check 与磁盘不一致；2 参数错。
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ass_rows  # noqa: E402

# 工序阶梯：文件名后缀 → 这一步叫什么。顺序即先后，不许乱排。
STAGES = [('base', '底本'), ('styled', '样式'), ('mech', '清稿'), ('final', '定稿')]
DONE = '达档'          # 成品目录里的文件，等于走完全部工序
UNKNOWN = '未知'

HEAD = ('> **本文件由 `tools/gen_progress.py` 从磁盘产物生成，禁止手改。**\n'
        '> 改状态 = 改产物，然后重跑脚本。表里没有的，就是不存在的。\n'
        '> 生成时间：{ts} ｜ 进度 = 文件名能证明的最后一道工序 ｜ 条数 = 该产物的对话行数\n')


def ep_of(name: str):
    """`X.mech.ass` → (`X`, '清稿')；`X.ass` → (`X`, None)。认后缀，不认目录。"""
    for i, (suffix, label) in enumerate(STAGES):
        tail = f'.{suffix}.ass'
        if name.endswith(tail):
            return name[:-len(tail)], i, label
    return (name[:-4] if name.endswith('.ass') else name), None, None


def scan(work: Path, final: Path):
    """返回 {集: {rank, label, path, seen}}。rank 越大越靠后；达档排最后。

    `seen` 记这一集出现过哪些阶——**只留最高阶会丢掉「中间断没断」的证据**，
    第一版就是这么写错的：base/styled/mech 三份都在，也报「缺样式」。
    """
    found = {}
    for p in sorted(work.rglob('*.ass')):
        ep, rank, label = ep_of(p.name)
        if rank is None:
            continue                     # 未分阶段的原始文件，不进台账
        cur = found.get(ep)
        if not cur:
            found[ep] = {'rank': rank, 'label': label, 'path': p, 'seen': {rank}}
            continue
        cur['seen'].add(rank)
        if rank > cur['rank']:
            cur.update({'rank': rank, 'label': label, 'path': p})
    if final.is_dir():
        for p in sorted(final.glob('*.ass')):
            found[p.stem] = {'rank': len(STAGES), 'label': DONE, 'path': p, 'seen': set()}
    return found


def damaged(found) -> list:
    """有后序无前序 = 状态损坏（`mech` 在、`styled` 不在）。

    不报的话，人会拿一个「说不清怎么来的」产物当依据往下做。串行工序里
    缺紧邻前序只有一个解释：文件被单独拷进来 / 被删过——两种情况都得人看一眼。
    **只查紧邻前序，且达档不查**：中间阶被清过是常态（重跑后旧层删掉），
    而达档是终态，前面清没清过不影响它已经达成。查宽了就是一片假警。
    """
    out = []
    for ep, v in found.items():
        if v['rank'] >= len(STAGES):          # 达档：不查
            continue
        if v['rank'] == 0:                    # 只有底本，前面没工序可缺
            continue
        if (v['rank'] - 1) not in v['seen']:
            out.append((ep, v['label'], f'缺 {STAGES[v["rank"] - 1][1]}'))
    return out


def rows(found):
    out = []
    for ep, v in sorted(found.items()):
        try:
            n = len(ass_rows.iter_rows(v['path']))
        except Exception as e:                      # noqa: BLE001
            n = f'读不出（{type(e).__name__}）'
        mtime = datetime.fromtimestamp(v['path'].stat().st_mtime).strftime('%m-%d %H:%M')
        out.append((ep, v['label'], n, mtime))
    return out


def render(found) -> str:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    table = rows(found)
    bad = {ep: why for ep, _, why in damaged(found)}
    lines = ['# 进度台账 · %d 集' % len(found), '', HEAD.format(ts=ts).rstrip('\n'), '',
             '| 集 | 进度 | 条数 | 最后变动 |', '|---|---|---|---|']
    for ep, label, n, mtime in table:
        mark = f'{label} ⚠ {bad[ep]}' if ep in bad else label
        lines.append(f'| `{ep}` | {mark} | {n} | {mtime} |')

    lines += ['', '## 汇总', '', '| 进度 | 集数 |', '|---|---|']
    tally = {}
    for ep, label, _, _ in table:
        tally[label] = tally.get(label, 0) + 1
    for label, k in sorted(tally.items(), key=lambda x: -x[1]):
        lines.append(f'| {label} | {k} |')
    lines.append(f'| **合计** | **{len(table)}** |')
    if bad:
        lines += ['', '**⚠ %d 集状态损坏**：有后序产物、缺前序。多半是文件被单独拷进来'
                      '或被删过，别照这张表往下排活，先人看一眼。' % len(bad)]
    return '\n'.join(lines) + '\n'


def strip_ts(text: str) -> str:
    return '\n'.join(ln for ln in text.splitlines() if not ln.startswith('> 生成时间：'))


def main() -> int:
    ap = argparse.ArgumentParser(description='进度台账生成器：扫产物出表，不判断质量')
    ap.add_argument('work', help='工作区目录（递归扫 *.base/.styled/.mech/.final.ass）')
    ap.add_argument('--final', help='成品目录（里面的 *.ass 记为「达档」）')
    ap.add_argument('--out', help='台账输出路径（不填只打印摘要）')
    ap.add_argument('--stdout', action='store_true', help='把整张表打到标准输出，不写文件')
    ap.add_argument('--check', action='store_true', help='与 --out 现有内容比对，不一致退 1')
    a = ap.parse_args()

    work = Path(a.work)
    if not work.is_dir():
        print(f'[ERROR] 工作区目录不存在：{work}')
        return 2
    final = Path(a.final) if a.final else Path()

    found = scan(work, final)
    if not found:
        print(f'[ERROR] {work} 下没有认得出的产物（*.base/.styled/.mech/.final.ass）——'
              f'先确认工序产物的命名，别让台账假装是空的')
        return 2

    text = render(found)
    if a.stdout or not a.out:
        print(text, end='')
    if a.out and not a.stdout:
        out = Path(a.out)
        if a.check:
            if not out.is_file():
                print(f'[ERROR] --check 要给一份已存在的台账：{out}')
                return 2
            old = out.read_text(encoding='utf-8')
            if strip_ts(old).strip() != strip_ts(text).strip():
                print(f'[不一致] {out} 与磁盘产物不符——台账落后了，重跑一次覆盖')
                return 1
            print(f'[一致] {out} 与磁盘产物相同')
            return 0
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding='utf-8', newline='\n')
        print(f'台账已写：{out}（{len(found)} 集）')

    if found and not a.stdout:
        tally = {}
        for _, label, _, _ in rows(found):
            tally[label] = tally.get(label, 0) + 1
        print('分档：' + ' / '.join(f'{k} {v}' for k, v in sorted(tally.items(), key=lambda x: -x[1])))
    bad = damaged(found)
    if bad:
        print(f'[警告] {len(bad)} 集状态损坏：' + '、'.join(ep for ep, _, _ in bad))
    return 0


if __name__ == '__main__':
    sys.exit(main())
