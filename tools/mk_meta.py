#!/usr/bin/env python
# 最近修改：2026-11-08（新建）
# -*- coding: utf-8 -*-
"""从分片表和源字幕生成 meta.json——通读分片的总账。

建来补一个三方口径不一致的缺口：手册把 `meta.json` 列为必需产物，但 `mk_worksheet.py`
只管一个行号区间、不知道全片多少条，而 `check_slices.py` 又把 meta.json 当可选项。
结果是每集都得手写一份，字段名只能翻源码猜（`skipped` 必须是对象，写成数字 `0`
会直接 `AttributeError: 'int' object has no attribute 'values'`——2026-11-08 踩过）。

**只算数得出来的**：总条数、各片行数与首末行号、跳过差额。
`cut`（切点为什么选这里）算不出来，本工具不产那个键；目标文件里已有的人工键**原样保留**。

用法：
  python mk_meta.py <集目录> <源ass> [--ep S03E05] [--skip 片头=21 --skip 回顾段=39] [--force]

  · 不给 --skip，跳过数写成 `{"未归类": N}`；给了就按名字分，合计必须对得上
  · 不给 --ep，从源文件名里认 SxxExx
"""
import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ass_rows as D  # noqa: E402  # 本库解析，条数口径与 findings 同源

SLICE_RE = re.compile(r'^片(\d+)\.tsv$')
EP_RE = re.compile(r'(S\d+E\d+)', re.I)


def fail(msg):
    print('  ERROR ' + msg, file=sys.stderr)
    sys.exit(1)


def read_slice(p):
    """一片 tsv → {file, start_line, end_line, rows}。行号取第一列，与 findings 同源。"""
    with io.open(p, encoding='utf-8-sig', newline='') as f:
        rd = [r for r in csv.reader(f, delimiter='\t')]
    body = [r for r in rd[1:] if r and r[0].strip()]
    if not body:
        fail('%s 只有表头没有数据行' % p.name)
    try:
        first, last = int(body[0][0]), int(body[-1][0])
    except ValueError:
        fail('%s 第一列不是行号（应是 ASS 物理行号）' % p.name)
    return {'file': p.name, 'start_line': first, 'end_line': last, 'rows': len(body)}


def main():
    ap = argparse.ArgumentParser(description='生成通读分片的 meta.json（总条数 / 各片行数 / 跳过差额）')
    ap.add_argument('ep_dir', help='集目录，里面是 片N.tsv')
    ap.add_argument('ass', help='源 ASS（数总条数用）')
    ap.add_argument('--ep', default='', help='集号，如 S03E05；不给就从源文件名认')
    ap.add_argument('--show', default='正传', help='剧型，默认 正传')
    ap.add_argument('--skip', action='append', default=[], metavar='名字=行数',
                    help='跳过明细，可重复；不给就写成一笔「未归类」')
    ap.add_argument('--force', action='store_true', help='已有 meta.json 也重写（人工键仍保留）')
    a = ap.parse_args()

    d = Path(a.ep_dir)
    if not d.is_dir():
        fail('不是目录：%s' % d)
    src = Path(a.ass)
    if not src.exists():
        fail('源字幕不在：%s' % src)

    files = sorted((p for p in d.iterdir() if SLICE_RE.match(p.name)),
                   key=lambda p: int(SLICE_RE.match(p.name).group(1)))
    if not files:
        fail('%s 里没有 片N.tsv' % d)

    slices = [read_slice(p) for p in files]
    total = sum(1 for _ in D.iter_rows(str(src)))
    if not total:
        fail('源字幕里一条对白都没有：%s' % src)

    got = sum(s['rows'] for s in slices)
    if got > total:
        fail('各片相加 %d 条 > 源字幕 %d 条——片切错了，或者源给错了' % (got, total))
    rest = total - got

    skipped = {}
    for item in a.skip:
        name, sep, n = item.partition('=')
        if not sep or not name.strip():
            fail('--skip 要写成 名字=行数，收到 %r' % item)
        try:
            skipped[name.strip()] = int(n)
        except ValueError:
            fail('--skip 的行数不是整数：%r' % item)
    if skipped:
        if sum(skipped.values()) != rest:
            fail('跳过明细合计 %d ≠ 差额 %d（源 %d − 各片 %d）'
                 % (sum(skipped.values()), rest, total, got))
    elif rest:
        skipped = {'未归类': rest}

    out = d / 'meta.json'
    if out.exists() and not a.force:
        fail('%s 已存在；确认要重算就加 --force（里面的人工键如 cut 会保留）' % out)

    meta = {}
    if out.exists():
        try:
            meta = json.loads(out.read_text(encoding='utf-8'))
        except Exception:
            meta = {}          # 旧文件坏了就当没有，但下面的 --force 才走到这儿
    meta.update({
        'show': a.show,
        'ep': a.ep or (EP_RE.search(src.name).group(1).upper() if EP_RE.search(src.name) else ''),
        'source': src.name,
        # 口径（手册「三、备料」+ check_slices 实测）：meta.rows = 各片行数相加，
        # **skipped 不在其中**。写成源字幕总条数会让 check_slices 报「总行数对不上」。
        'rows': got,
        'skipped': skipped,
        'slices': slices,
    })
    out.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    print('meta.json：rows=%d（各片相加）  源 %d  skipped=%d  片数=%d  → %s'
          % (got, total, sum(skipped.values()), len(slices), out))
    print('  rows = 各片行数相加，**不含** skipped（口径见手册「三、备料」）。'
          '两个量各管一件事：rows 验片切得对不对，skipped 验有没有漏段。')
    for s in slices:
        print('  %s  L%d–L%d  %d 行' % (s['file'], s['start_line'], s['end_line'], s['rows']))
    if not meta.get('cut'):
        print('  提醒：cut（切点为什么选这里）算不出来，没写。要留痕就手补这个键，重跑 --force 不会冲掉。')


if __name__ == '__main__':
    main()
