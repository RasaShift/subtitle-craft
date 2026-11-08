#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-08（新建：findings → 改动表，补上 evidence 断链）
"""findings2tsv.py -- findings JSON → 改动表 TSV（给 land_verify 吃）

为什么有它：`findings/<集>.json` 的字段表很全（见 `docs/findings写法.md`），
但落地吃的是改动表（`no / old / new`），两者之间一直没有工具——靠人对照合并。
人合并的后果是 `evidence` 整段丢：改动表格式里**根本没有那一列**，验收第 6、7 项
（evidence 空 / 查无出处）就成了纯人读。剧库四组实验各写各的格式，都栽在这一步。

本工具只做**纯映射**，不做判断、不做合并：
  findings[].ln        → no
  findings[].cn        → old
  findings[].cn_new    → new
  findings[].source_model → rule（字段名保持旧名，见判定记录 11-06）
  findings[].evidence  → evidence（**改动表新增的可选列 2026-11-08**）
  inserts[]            → 另写一张补行表（`--ins`）

不做的，会打印出来，别当成做了：
  · `cn_new` 为空的条目跳过（merge 会静默丢弃，写了也白写）
  · **只改英文轨**（有 `en_after`、中文没变）的条目：改动表按整行文本替换，
    这类要人把中英拼成一行，本工具不猜，报数交人工
  · 合并：对稿定案的改动 + 审稿清单仍是人工并成一张，本工具只管 findings 这一路

用法：
  python findings2tsv.py <findings.json> --tsv <改动表.tsv> [--ins <补行表.tsv>]
退出码：0 成功 / 2 输入有问题 / 1 一条可用条目都没有
"""
import argparse
import json
import pathlib
import sys

COLS = ['no', 'old', 'new', 'rule', 'evidence']
INS_COLS = ['after_no', 'cn', 'en']


def clean(s):
    """制表符 / 换行会撕碎 tsv，压成空格。"""
    return ' '.join(str(s or '').split())


def main():
    ap = argparse.ArgumentParser(description='findings JSON → 改动表 TSV')
    ap.add_argument('findings', help='findings/<集>.json')
    ap.add_argument('--tsv', required=True, help='输出改动表')
    ap.add_argument('--ins', help='输出补行表（json 里有 inserts 时用）')
    a = ap.parse_args()

    src = pathlib.Path(a.findings)
    if not src.is_file():
        sys.exit(f'找不到：{src}')
    try:
        data = json.loads(src.read_text(encoding='utf-8-sig'))
    except json.JSONDecodeError as e:
        sys.exit(f'{src.name} 不是合法 JSON：{e}')

    rows = data.get('findings') or []
    ins = data.get('inserts') or []
    out, skipped_empty, en_only, no_ev = [], 0, [], 0

    for r in rows:
        ln, cn, new = r.get('ln'), clean(r.get('cn')), clean(r.get('cn_new'))
        if ln in (None, ''):
            sys.exit(f'有条目缺 `ln`（源字幕行号）：{json.dumps(r, ensure_ascii=False)[:80]}')
        if not new:
            skipped_empty += 1
            continue
        if new == clean(r.get('cn')) and r.get('en_after'):
            en_only.append(ln)
            continue
        ev = clean(r.get('evidence'))
        if not ev:
            no_ev += 1
        out.append((str(ln), clean(r.get('cn')), new,
                    clean(r.get('source_model')), ev))

    if not out and not ins:
        sys.exit('没有可落地条目：`cn_new` 全为空，或全是要改英文轨的')

    dst = pathlib.Path(a.tsv)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\t'.join(COLS) + '\n')
        for row in out:
            f.write('\t'.join(row) + '\n')
    print(f'{src.name}: 出改动表 {len(out)} 条 → {dst}')

    if ins:
        p = pathlib.Path(a.ins) if a.ins else dst.with_name(dst.stem + '.ins.tsv')
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'w', encoding='utf-8', newline='\n') as f:
            f.write('\t'.join(INS_COLS) + '\n')
            for r in ins:
                f.write('\t'.join([clean(r.get('after_no') or r.get('after_ln')),
                                   clean(r.get('cn')), clean(r.get('en'))]) + '\n')
        print(f'  补行表 {len(ins)} 条 → {p}')
    elif a.ins:
        print('  [提示] 给了 --ins 但 json 里没有 inserts，没写补行表')

    if skipped_empty:
        print(f'  [跳过] {skipped_empty} 条 `cn_new` 为空——落地会静默丢弃，写了也白写')
    if en_only:
        print(f'  [人工] {len(en_only)} 条只改英文轨（行号 {", ".join(map(str, en_only[:6]))}'
              f'{"…" if len(en_only) > 6 else ""}）：改动表按整行替换，要把中英拼成一行，本工具不猜')
    if no_ev:
        print(f'  [警告] {no_ev} 条没有 `evidence`——验收第 6、7 项会报红，回去补依据')
    return 0


if __name__ == '__main__':
    sys.exit(main())
