#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-05（新建：双语字幕直出配对表，省掉「先找第二条轨」的门槛）
"""bilingual_pairs.py -- 一份双语字幕 → 配对表（给 triage_diff 吃）

为什么要它：多数人手上是**一份中英双语字幕**，不是两条独立轨道。
逼他们先凑出两条轨再对齐，门槛高到当场劝退（分诊器已经能降级，入口不能再卡）。
双语字幕里中英本来就是同一条时间码，配对天然存在，不需要再按时间轴算一遍。

支持的两种双语形态：
  · ASS：一条事件里写「中文\\N{\\rEnglish}英文」（或 \\N 换行并列两行）
  · SRT：一个 cue 里中文一行、英文一行
样式名是 Mark / Sign 的事件（制作组署名）单独标成 `未配对:标识`，分诊会跳过。

用法：
  python bilingual_pairs.py <双语字幕.ass|.srt> --tsv <配对表.tsv>
  python bilingual_pairs.py <目录> --tsv <输出目录>       # 批量，按文件名一份

输出列与 align_bilingual.py 完全一致，后面接 triage_diff 即可：
  中文条号 / 中文开始 / 中文结束 / 中文文本 / 英文条号 / 英文开始 / 英文结束 /
  英文文本 / 置信度 / 标记
置信度恒为 1.00（同一条时间码，不存在配对误差），这是它和对齐器的区别：
**别拿它的输出跑对齐器的验收判据**，那是另一件事。

认不出双语的 cue（整条只有中文或只有英文）不丢：有中文就留行、标 `未配对:无候选`，
原样交给人看——静默丢弃比漏判贵。
"""
import argparse
import pathlib
import re
import sys

from align_bilingual import (decode, parse_srt, ms2tc, TAG, CREW, CJK,
                             kind_of, HEAD_WINDOW, TAIL_WINDOW)

ASS_TIME = re.compile(r'^(\d+):(\d{2}):(\d{2})[.:](\d{1,2})$')
ASS_SPLIT = re.compile(r'\\N\{\\r[^}]*\}')
LAT = re.compile(r'[A-Za-z]')
CREW_STYLE = {'mark', 'sign', 'note'}


def ass_ms(t):
    m = ASS_TIME.match(t.strip())
    if not m:
        return None
    h, mi, s, cs = m.groups()
    return (int(h) * 3600 + int(mi) * 60 + int(s)) * 1000 + int(cs.ljust(2, '0')[:2]) * 10


def parse_ass(path):
    """ASS 的 Dialogue 行 → [{no, s, e, text, style}]。非 Dialogue 行一律跳过。"""
    cues = []
    for line in decode(path).splitlines():
        if not line.lower().startswith('dialogue:'):
            continue
        parts = line.split(',', 9)
        if len(parts) < 10:
            continue
        s, e = ass_ms(parts[1]), ass_ms(parts[2])
        if s is None or e is None:
            continue
        cues.append({'no': len(cues) + 1, 's': s, 'e': e,
                     'style': parts[3].strip(), 'text': parts[9]})
    return cues


def split_bilingual(text):
    """把一条 cue 切成 (中文, 英文)，**不清标签**——「是不是卡」要看原标签，见 kind_of。

    顺序不能反：先清样式标签的话 `{\\rEnglish}` 会被一起吃掉，整条切不开
    （2026-11-05 写临时脚本时踩过，成品全被判成「无英文」）。

    **判断语种看的是原串，不是去标签后的串**——这是有意的：英文段挂着中文内联标签
    （`{\\fn黑体}` 这类）说明这份字幕还没跑 MF6，切不开是对的，`ass_rows` 会据此报警
    让人先去跑清稿文件层。别为了方便把它改成看去标签的串，那会把提示一起改没
    （2026-11-07 实跑一部单文件双语剧时试过，改完 973 条能切出 952 条，但 `ass_rows`
    的两条「拆不开要报警」断言当场失败——那正是提示机制被拆掉的证据）。
    """
    text = text.replace('\\h', ' ')
    if ASS_SPLIT.search(text):
        zh, en = ASS_SPLIT.split(text, 1)
        return _raw(zh), _raw(en)
    zh, en = [], []
    for ln in re.split(r'\\N|\r?\n', text):
        if not ln.strip():
            continue
        (zh if CJK.search(ln) else en).append(ln)
    return _raw(' '.join(zh)), _raw(' '.join(en))


def _raw(s):
    return s.replace('\\N', ' ').replace('\\n', ' ').strip()


def _clean(s):
    return ' '.join(TAG.sub('', s).split())


def pairs_of(path):
    """一份字幕 → [{zh, zh_raw, en, s, e, style}]。SRT 走 align_bilingual 的解析器，别重写一份。"""
    if path.suffix.lower() == '.ass':
        cues = parse_ass(path)
    else:
        cues, _bad = parse_srt(path)
        for c in cues:
            c['style'] = ''
    out = []
    for c in cues:
        zh_raw, en_raw = split_bilingual(c['text'])
        zh, en = _clean(zh_raw), _clean(en_raw)
        if not zh and not en:
            continue
        out.append({'no': c['no'], 's': c['s'], 'e': c['e'],
                    'zh': zh, 'zh_raw': zh_raw, 'en': en, 'style': c.get('style', '')})
    return out


HEAD = ['中文条号', '中文开始', '中文结束', '中文文本', '英文条号', '英文开始',
        '英文结束', '英文文本', '置信度', '标记']


def flag_of(r, head_window, tail_window):
    """标记列。判「是不是卡」直接复用对齐器的 kind_of——同一条判据不能有两份。"""
    plain = TAG.sub('', r['zh_raw'])
    if r['style'].lower() in CREW_STYLE:
        return '未配对:标识'
    if CREW.search(plain) and len(CJK.findall(plain)) < 40:
        return '未配对:标识'
    if r['en']:
        return ''
    return kind_of({'text': r['zh_raw'], 's': r['s'], 'e': r['e']}, head_window, tail_window)


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tail = max((r['e'] for r in rows), default=0) - TAIL_WINDOW * 1000
    with open(path, 'w', encoding='utf-8-sig', newline='\n') as f:
        f.write('\t'.join(HEAD) + '\n')
        for i, r in enumerate(rows, 1):
            has_en = bool(r['en'])
            f.write('\t'.join([
                str(i), ms2tc(r['s']), ms2tc(r['e']), r['zh'],
                str(i) if has_en else '', ms2tc(r['s']) if has_en else '',
                ms2tc(r['e']) if has_en else '', r['en'],
                '1.00' if has_en else '', flag_of(r, HEAD_WINDOW * 1000, tail)]) + '\n')


def main():
    ap = argparse.ArgumentParser(description='双语字幕（ASS/SRT）→ 配对表，给 triage_diff 用')
    ap.add_argument('src', help='双语字幕文件，或批量目录')
    ap.add_argument('--tsv', required=True, help='输出 tsv；批量模式下是输出目录')
    a = ap.parse_args()
    src = pathlib.Path(a.src)
    dst = pathlib.Path(a.tsv)

    if src.is_dir():
        files = sorted([p for p in src.iterdir() if p.suffix.lower() in ('.ass', '.srt')])
        if not files:
            sys.exit(f'{src} 下没有 .ass / .srt')
        for p in files:
            rows = pairs_of(p)
            write(dst / (p.stem + '.pairs.tsv'), rows)
            print(f'{p.name}: {len(rows)} 条，有英文 {sum(1 for r in rows if r["en"])}')
        return

    rows = pairs_of(src)
    if not rows:
        sys.exit(f'没解析出任何一条：{src}')
    write(dst, rows)
    n_en = sum(1 for r in rows if r['en'])
    print(f'{src.name}: 共 {len(rows)} 条，有英文 {n_en}')
    print(f'  配对表：{dst}')
    if n_en == 0:
        print('  [警告] 一条英文都没认出来——多半不是双语轨，或分隔符不是「中文\\N{\\rEnglish}英文」')


if __name__ == '__main__':
    main()
