#!/usr/bin/env python
# 最近修改：未记录 2026-11-01（2026-11-01 补署名；此前无此行，原改动内容无据可查）
# -*- coding: utf-8 -*-
"""把 ASS 的一段对白导成通读工作表（行号 / 时间码 / 中文 / 英文）。

建来把一段对白导成「行号 / 时间码 / 中文 / 英文」四列工作表，跨剧通用。
**解析一律复用 `ass_rows.iter_rows`**，不自己切 `\\N{\\rEnglish}`——自己切会和 findings 那边
的 cn/en 对不上（实测：自切会得到空中文，统一解析才有内容）。

行号 = ASS 文件里的**物理行号**，与 recap_boundary.json / findings 一致。

用法：
  python mk_worksheet.py <ass> <起行> <止行> <输出tsv>
"""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ass_rows as D  # noqa: E402  # 本库解析，行号与 findings 同源


def main():
    ass, lo, hi, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    rows = [r for r in D.iter_rows(ass) if lo <= r['ln'] <= hi]
    with io.open(out, 'w', encoding='utf-8-sig', newline='\r\n') as f:
        f.write('行号\t时间码\t中文\t英文\n')
        for r in rows:
            f.write('%d\t%s\t%s\t%s\n' % (r['ln'], r['start'], r['cn'], r['en']))
    print('%s  %d 行  L%d–L%d  首=%s  末=%s' % (out, len(rows), rows[0]['ln'], rows[-1]['ln'],
                                             rows[0]['start'], rows[-1]['start']))
    bad = [r['ln'] for r in rows if not r['cn'].strip()]
    if bad:
        print('  ⚠ 中文为空 %d 条：%s' % (len(bad), bad))


if __name__ == '__main__':
    main()
