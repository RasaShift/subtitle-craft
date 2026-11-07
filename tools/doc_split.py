#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-05（新建：把「拆超长文档」压成一条命令）
"""doc_split.py -- 按节切分、把指定节整段搬到另一个文件，原位置留一行指针

为什么有它：`doc_lint.py` 只能「发现」超长，发现之后靠人手搬。手搬过一次的人
就不会想搬第二次（原库实测：1922 行拆成 353 行，纯手工近 20 分钟）。于是超限
就变成「知道该拆但拖着不拆」——体检天天报红，慢慢就没人看了。

本脚本**不判断哪段是结论、哪段是过程**（机器判不了，只有人能判）。它只做搬运：
  1. 按标题切节，把超长的列出来（带序号、行数）
  2. 人挑序号，一条命令把这几节整段搬走，原位置留一行指针

用法：
    python tools/doc_split.py <源文件>                    # 列超长节（默认 >8 行）
    python tools/doc_split.py <源文件> --threshold 12     # 换阈值
    python tools/doc_split.py <源文件> --list             # 列全部节，不筛
    python tools/doc_split.py <源文件> --move 3,7 --to docs/archive/实测流水_20261105.md

搬的时候保持目标文件原有的换行符；目标不存在就新建（LF）。
"""
import argparse
import re
import sys
from pathlib import Path

HEAD = re.compile(r'^(#{1,6})\s')


def read_lines(p: Path):
    raw = p.read_bytes()
    nl = '\r\n' if b'\r\n' in raw else '\n'
    text = raw.decode('utf-8-sig').replace('\r\n', '\n')
    return text.split('\n'), nl


def write_lines(p: Path, lines, nl):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes((nl.join(lines)).encode('utf-8'))


def split_sections(lines):
    """返回 [(起始索引, 结束索引(不含), 标题, 行数)]，索引 0-based。"""
    marks = [(i, ln) for i, ln in enumerate(lines) if HEAD.match(ln)]
    out = []
    for k, (i, ln) in enumerate(marks):
        j = marks[k + 1][0] if k + 1 < len(marks) else len(lines)
        body = [x for x in lines[i:j] if x.strip()]
        out.append((i, j, ln.strip(), len(body)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description='把超长文档的某些节整段搬走，原位置留指针')
    ap.add_argument('src')
    ap.add_argument('--threshold', type=int, default=8, help='超过这么多非空行就算超长（默认 8）')
    ap.add_argument('--list', action='store_true', help='列全部节，不按阈值筛')
    ap.add_argument('--move', help='要搬走的节序号，逗号分隔，如 3,7')
    ap.add_argument('--to', help='搬到哪个文件')
    a = ap.parse_args()

    src = Path(a.src)
    if not src.is_file():
        sys.exit(f'文件不存在：{src}')
    lines, nl = read_lines(src)
    secs = split_sections(lines)
    if not secs:
        sys.exit('这个文件没有标题，切不出节')

    if not a.move:
        for k, (i, j, title, n) in enumerate(secs, 1):
            if a.list or n > a.threshold:
                print(f'{k:3}  L{i + 1:>4}  {n:>4} 行  {title[:60]}')
        if not a.list:
            print(f'\n只列了超过 {a.threshold} 行的节，共 {len(secs)} 节。'
                  f'搬：--move 序号,序号 --to 目标文件')
        return 0

    if not a.to:
        sys.exit('--move 要配 --to')

    try:
        picked = sorted({int(x) for x in a.move.split(',') if x.strip()})
    except ValueError:
        sys.exit(f'--move 读不懂：{a.move}（要逗号分隔的序号）')
    bad = [k for k in picked if not 1 <= k <= len(secs)]
    if bad:
        sys.exit(f'序号越界：{bad}（共 {len(secs)} 节）')

    dst = Path(a.to)
    moved_text, pointers, drop = [], [], set()
    for k in picked:
        i, j, title, n = secs[k - 1]
        moved_text.append('\n'.join(lines[i:j]).rstrip())
        # 指针后面留一个空行，不然紧跟下一节标题会粘在一起
        pointers.append((i, j, f'> 本节已搬到 `{dst.as_posix()}`（{n} 行）。\n'))
        drop.update(range(i, j))

    out = []
    for idx, ln in enumerate(lines):
        for (i, j, ptr) in pointers:
            if idx == i:
                out.append(ptr)
        if idx in drop:
            continue
        out.append(ln)

    if dst.is_file():
        dlines, dnl = read_lines(dst)
        tail = dlines[:]
        while tail and not tail[-1].strip():
            tail.pop()
        # 多节之间也留空行
        sep = []
        for k, block in enumerate(moved_text):
            sep.append('')
            sep.append(block)
        write_lines(dst, tail + sep, dnl)
    else:
        body = []
        for k, block in enumerate(moved_text):
            if k:
                body.append('')
            body.append(block)
        write_lines(dst, body, '\n')

    write_lines(src, out, nl)
    head = len([x for x in lines if x.strip()])
    now = len([x for x in out if x.strip()])
    print(f'已搬 {len(picked)} 节 → {dst}')
    print(f'源文件从 {head} 行降到 {now} 行（按非空行算），原位置留了 {len(pointers)} 行指针')
    return 0


if __name__ == '__main__':
    sys.exit(main())
