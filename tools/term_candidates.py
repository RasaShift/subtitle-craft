#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-05（新建：术语/专名候选提取，只挑候选不给译法）
"""term_candidates.py -- 从字幕里挑术语 / 专名候选（只挑候选，不给译法）

为什么有它：攒词表最难的是「从零想起有哪些专名」。老库那套是**人手写进代码常量**
（`CANON_NAMES` / `SHOW_CANON`），`build_terminology.py` 只负责把常量导成 JSON 给机器查，
**没有任何一步是「从字幕里挑」**。换一部新剧，这步只能靠人一集集边看边记。

本脚本只做第一步：把「像专名的东西」连同出现次数、首现位置挑出来，交人定译。
**不许它给建议译名**——机器只挑候选、人定案，这是本库第四条红线。

挑什么（都是启发式，会有假红，靠人筛）：
  · 大写开头的词（句中）：`Morgan`、`Harrison`
  · 两到三个大写词连读：`Miami Metro`、`New Blood`
  · 全大写缩写（2 字母以上）：`FBI`、`CMG`
不挑：句首常见词、称谓（Mr / Mrs / Dr / Sir）、语气词、代词。

用法：
    python term_candidates.py <字幕.srt|.ass ...> [--min 2] [--tsv 输出.tsv]
    python term_candidates.py <源字幕目录> --min 2 --tsv <输出>/词表候选.tsv

输出的表就是给人填的底稿：候选 / 次数 / 首现 / 型 / 中文片段（同一条的字幕原文，帮你认人）。
**填完的成品表要三列**：原文 / 规定译法 / 已见错写法（见 docs/工序-清稿-规则清单.md MT1）。
词表内容一律留各剧库工作区，永不出门。
"""
import argparse
import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from land_verify import load                                   # noqa: E402

LAT = re.compile(r"[A-Za-z][A-Za-z'\-]*")
ABBR = re.compile(r'\b[A-Z]{2,}\b')
TAG = re.compile(r'\{[^}]*\}')
ESC = re.compile(r'\\[Nnh]')          # ASS 的换行转义 \N \n \h——不除掉会挑出一堆 "N"
STOP = {
    'I', 'A', 'An', 'The', 'Mr', 'Mrs', 'Ms', 'Miss', 'Dr', 'Sir', 'Madam', 'Lady', 'Lord',
    'You', 'We', 'They', 'He', 'She', 'It', 'Me', 'Him', 'Her', 'Us', 'Them',
    'My', 'Your', 'His', 'Its', 'Our', 'Their', 'Mine', 'Yours',
    'What', 'Why', 'How', 'Who', 'When', 'Where', 'Which', 'That', 'This', 'These', 'Those',
    'Yeah', 'Yes', 'No', 'Not', 'Oh', 'Ah', 'Hey', 'Hi', 'Hello', 'Well', 'OK', 'Okay',
    'God', 'Please', 'Thanks', 'Thank', 'Sorry', 'Excuse', 'Wait', 'Look', 'Come', 'Let',
    'Good', 'Bad', 'Great', 'Fine', 'Nice', 'Right', 'Wrong', 'Dear', 'Love', 'Sure',
    'Get', 'Go', 'Do', 'Does', 'Did', 'Is', 'Are', 'Was', 'Were', 'Be', 'Been', 'Have', 'Has',
    'Had', 'Will', 'Would', 'Should', 'Could', 'Can', 'May', 'Might', 'Must', 'Shall',
    'Because', 'But', 'And', 'Or', 'If', 'So', 'Then', 'Now', 'Here', 'There', 'Just',
    'Of', 'In', 'On', 'At', 'For', 'To', 'From', 'With', 'By', 'About', 'After', 'Before',
    'All', 'Any', 'Some', 'Every', 'Each', 'Both', 'More', 'Most', 'Much', 'Many',
    'Only', 'Also', 'Too', 'Very', 'Still', 'Even', 'Again', 'Once', 'Never', 'Always',
    'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight', 'Nine', 'Ten',
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday',
    'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
    'September', 'October', 'November', 'December', 'Christmas', 'Easter',
}


def clean(s):
    """去掉 ASS 覆盖标签与换行转义。\\N 不除掉的话，每集都能挑出上千个 "N"。"""
    return ESC.sub(' ', TAG.sub(' ', s or ''))


def base(tok):
    """I'm / Don't / Minister's → 取撇号前那截，用来看是不是常见词。"""
    return re.split(r"['’]", tok)[0]


def candidates_of(text, max_tokens=3):
    """一条字幕里挑出的候选（1–3 个连续大写词 + 全大写缩写）。返回 set。"""
    text = clean(text)
    out = set()
    for m in ABBR.finditer(text):
        out.add(m.group(0))
    words = LAT.findall(text)
    # 带撇号的一律不算专名（Don't / Minister's / I'm）——它们只有词形变化，不是名字
    caps = [w for w in words if w[:1].isupper() and "'" not in w and '’' not in w]
    for n in range(1, max_tokens + 1):
        for i in range(len(caps) - n + 1):
            seq = caps[i:i + n]
            if base(seq[0]) in STOP or base(seq[-1]) in STOP:
                continue                       # 两头是常见词就不成串（"Minister I" 这类半句）
            if all(base(w) in STOP for w in seq):
                continue
            out.add(' '.join(seq))
    return out


def scan(paths, min_count=2, max_tokens=3, with_zh=True):
    """{候选: {'n': 次数, 'first': '文件:条号', 'zh': 首现那条的中文片段}}"""
    acc = collections.OrderedDict()
    for p in paths:
        try:
            _, cues, _ = load(p)
        except Exception as e:                     # 单个文件坏了不许连累整批
            print(f'  跳过 {p.name}：{e}', file=sys.stderr)
            continue
        for c in cues:
            en = c.get('en') or c.get('text') or ''
            zh = clean(c.get('zh') or '').strip()
            for cand in candidates_of(en, max_tokens):
                r = acc.setdefault(cand, {'n': 0, 'first': f'{p.name}:{c["no"]}',
                                          'zh': zh[:24]})
                r['n'] += 1
    rows = [(k, v) for k, v in acc.items() if v['n'] >= min_count]
    rows.sort(key=lambda kv: (-kv[1]['n'], kv[0]))
    return rows


def kind_of(name):
    return '缩写' if ABBR.fullmatch(name) else ('多词' if ' ' in name else '单词')


def drop_fragments(rows, ratio=0.5):
    """把「其实是长词的碎片」的单单词去掉：Prime(780) 几乎全在 Prime Minister(719) 里。

    两头都要看：`Prime` 是长词的头，`Service` 是 `Civil Service` 的尾，只查头会漏掉后者。
    不这么做的后果是底稿里全是 Prime / Service 这类半截词，人得自己配对，那正是最费神的地方。
    只占长词三分之一的情况（Minister 之于 Prime Minister）会留着——它自己也是术语。
    """
    ends = {}
    for name, r in rows:
        if ' ' in name:
            for w in (name.split(' ')[0], name.split(' ')[-1]):
                ends[w] = ends.get(w, 0) + r['n']
    out = [(n, r) for n, r in rows if ' ' in n or ends.get(n, 0) < r['n'] * ratio]
    return out, len(rows) - len(out)


def main():
    ap = argparse.ArgumentParser(description='术语/专名候选提取（只挑候选，不给译法）')
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--min', type=int, default=2, help='至少出现几次才列（默认 2，出现一次的不值得收）')
    ap.add_argument('--max-tokens', type=int, default=3, help='最多几个词连读（默认 3）')
    ap.add_argument('--tsv')
    ap.add_argument('--no-zh', action='store_true', help='不带中文片段（免得真实台词外流）')
    a = ap.parse_args()

    files = []
    for x in a.paths:
        p = Path(x)
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in ('.ass', '.ssa', '.srt'))
        elif p.is_file():
            files.append(p)
        else:
            sys.exit(f'路径不存在：{p}')
    if not files:
        sys.exit('没有 .ass / .ssa / .srt 文件')

    rows = scan(files, a.min, a.max_tokens)
    rows, dropped = drop_fragments(rows)
    print(f'扫 {len(files)} 个文件，出现 ≥{a.min} 次的候选 {len(rows)} 条'
          + (f'（另有 {dropped} 条单单词被当成长词碎片略去）' if dropped else ''))

    cols = ['候选', '次数', '首现', '型'] + ([] if a.no_zh else ['中文片段'])
    print('| ' + ' | '.join(cols) + ' |')
    print('|' + '---|' * len(cols))
    for name, r in rows[:40]:
        vals = [name, str(r['n']), r['first'], kind_of(name)] + ([] if a.no_zh else [r['zh']])
        print('| ' + ' | '.join(vals) + ' |')
    if len(rows) > 40:
        print(f'  （只打印前 40 条，完整清单看 --tsv；共 {len(rows)} 条）')

    if a.tsv:
        with open(a.tsv, 'w', encoding='utf-8-sig', newline='\n') as f:
            f.write('\t'.join(['候选', '次数', '首现', '型', '规定译法', '已见错写法', '备注'])
                    + '\n')
            for name, r in rows:
                f.write('\t'.join([name, str(r['n']), r['first'], kind_of(name), '', '', ''])
                        + '\n')
        print(f'\n底稿已写：{a.tsv}（后三列留空，交人填）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
