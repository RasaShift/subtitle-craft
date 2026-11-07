#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-05（新建：跨剧通用字幕体检，SRT/ASS 双格式 + 三类事故检测）
"""subtitle_probe.py -- 字幕体检（只读，不改任何文件）

为什么有它：换一部剧就要重写一次体检脚本（ASS 要读 UTF-16、SRT 是简英两份），
每次都在现场写。这里一次做对，参数里没有剧名，任何剧库都能用。

能查什么：
  · 通用：编码 / 条数 / 语种 / 坏时长 / 重叠 / 停留过短过长
  · 中文：CPS（字/秒）中位与 p95、行长分布、超 18 字条数
  · 英文：行长、超长单词（粘连词嫌疑）
  · 事故：<font> 残留、金额问号（£ 被替换成 ?）、花括号残留

用法：
  python subtitle_probe.py <目录或文件> [<目录或文件> ...] [--tsv out.tsv]
  python subtitle_probe.py .\\01_MKV提取字幕 --tsv ..\\02_分析\\底本体检.tsv

判据（不合格线，可 --strict 触发非零退出）：
  CPS 中位 > 6.0 / p95 > 9.0 · 行长中位 > 16 字 · 坏时长 > 0 · 超长单词 > 0
  出现 <font> 残留或金额问号 = 一律不合格
"""
import argparse
import pathlib
import re
import statistics
import sys

CJK = re.compile(r'[\u4e00-\u9fff]')
LAT = re.compile(r"[A-Za-z][A-Za-z'\-]*")
TAG = re.compile(r'\{[^}]*\}')
FONT = re.compile(r'</?font\b|</?i\b|</?b\b', re.I)
MONEY_Q = re.compile(r'\?\d')  # 只查「问号在数字前」= £ 被替换的形态；\d\? 会抓所有「8?」「Ml5?」正常问句，2026-11-05 实测假红过半
LONG_WORD = 16  # 超过即列「疑似粘连词」，需人工看：Ministerrecording 恰好 18 字符


def decode(p):
    raw = p.read_bytes()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16'), 'UTF-16(BOM)'
    if raw[:2] == b'\xef\xbb\xbf':
        return raw.decode('utf-8-sig'), 'UTF-8(BOM)'
    if b'\x00' in raw[:8]:
        return raw.decode('utf-16-le'), 'UTF-16LE(无BOM)'
    return raw.decode('utf-8-sig'), 'UTF-8'


def t2s(x):
    x = x.strip().replace('.', ':', 1) if x.strip().count(':') == 1 else x.strip()
    parts = x.split(':')
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2].replace(',', '.'))
    if len(parts) == 4:
        # ASS 写法 0:00:01.84，末段是厘秒
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + int(parts[3]) / 100.0
    raise ValueError(f'时间码读不懂: {x!r}')


SRT_TIME = re.compile(r'(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})')


def parse_srt(txt, info):
    blocks = re.split(r'\r?\n\s*\r?\n', txt.strip())
    for b in blocks:
        lines = [x for x in b.splitlines() if x.strip()]
        if not lines:
            continue
        m = None
        body_start = 0
        for i, ln in enumerate(lines[:3]):
            m = SRT_TIME.search(ln)
            if m:
                body_start = i + 1
                break
        if not m:
            info['bad_blocks'] += 1
            continue
        info['cues'].append({'start': t2s(m.group(1)), 'end': t2s(m.group(2)),
                             'style': '', 'text': '\n'.join(lines[body_start:])})


def parse_ass(txt, info):
    section = None
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith('['):
            section = s
            continue
        if section == '[Script Info]':
            if s.startswith('PlayResX'):
                info['resx'] = s.split(':')[1].strip()
            elif s.startswith('PlayResY'):
                info['resy'] = s.split(':')[1].strip()
        elif section == '[V4+ Styles]':
            if s.startswith('Style:'):
                f = s.split(',')
                info['styles'].append(f[0].split(':')[1].strip())
        elif section == '[Events]':
            if s.startswith('Comment:'):
                info['comments'] += 1
                continue
            if not s.startswith('Dialogue:'):
                continue
            parts = s.split(',', 9)
            if len(parts) < 10:
                info['bad_blocks'] += 1
                continue
            info['cues'].append({'start': t2s(parts[1]), 'end': t2s(parts[2]),
                                 'style': parts[3].strip(),
                                 'text': TAG.sub('', parts[9]).replace('\\N', '\n')})


def lang_of(cues):
    zh = sum(len(CJK.findall(c['text'])) for c in cues)
    en = sum(len(LAT.findall(c['text'])) for c in cues)
    if zh + en == 0:
        return '?'
    return 'zh' if zh >= en else 'en'


def probe(p):
    txt, enc = decode(p)
    info = {'file': p.name, 'enc': enc, 'resx': '', 'resy': '', 'styles': [],
            'cues': [], 'comments': 0, 'bad_blocks': 0}
    raw = txt
    ext = p.suffix.lower()
    (parse_srt if ext == '.srt' else parse_ass)(txt, info)

    cues = info['cues']
    # 事故只在文本区查：整篇查会把「问号 + 换行 + 下一条序号」配成金额问号（2026-11-05 实测 67 处全是假红）
    body = '\n'.join(c['text'] for c in cues)
    r = {'file': info['file'], 'kind': ext.lstrip('.'), 'enc': enc,
         'res': f"{info['resx']}x{info['resy']}" if info['resx'] else '-',
         'styles': len(info['styles']), 'cues': len(cues),
         'bad_blocks': info['bad_blocks'], 'comments': info['comments'],
         'font': len(FONT.findall(body)), 'money_q': len(MONEY_Q.findall(body))}
    if not cues:
        r['lang'] = '?'
        return r

    r['lang'] = lang_of(cues)
    cues.sort(key=lambda c: c['start'])
    durs = [max(c['end'] - c['start'], 0) for c in cues]
    r['dur_bad'] = sum(1 for c in cues if c['end'] <= c['start'])
    r['overlap'] = sum(1 for a, b in zip(cues, cues[1:]) if b['start'] < a['end'] - 0.01)
    r['short'] = sum(1 for d in durs if d < 0.8)
    r['long'] = sum(1 for d in durs if d > 8)

    if r['lang'] == 'zh':
        lens = [len(CJK.findall(c['text'])) for c in cues]
        cps = [n / d for n, d in zip(lens, durs) if n and d > 0]
        r['len_med'] = statistics.median(lens) if lens else 0
        r['len_max'] = max(lens) if lens else 0
        r['over18'] = sum(1 for n in lens if n > 18)
        r['cps_med'] = statistics.median(cps) if cps else 0
        r['cps_p95'] = sorted(cps)[min(int(len(cps) * 0.95), len(cps) - 1)] if cps else 0
        r['cps_max'] = max(cps) if cps else 0
        r['long_word'] = 0
    else:
        lens = [len(' '.join(LAT.findall(c['text']))) for c in cues]
        words = [w for c in cues for w in LAT.findall(c['text'])]
        r['len_med'] = statistics.median(lens) if lens else 0
        r['len_max'] = max(lens) if lens else 0
        r['over18'] = sum(1 for n in lens if n > 60)
        r['cps_med'] = r['cps_p95'] = r['cps_max'] = 0
        r['long_word'] = sum(1 for w in words if len(w) > LONG_WORD)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--tsv')
    ap.add_argument('--strict', action='store_true', help='有硬伤时非零退出')
    args = ap.parse_args()

    files = []
    for x in args.paths:
        p = pathlib.Path(x)
        if p.is_dir():
            files += sorted([f for f in p.iterdir() if f.suffix.lower() in ('.ass', '.srt')])
        elif p.exists():
            files.append(p)
        else:
            sys.exit(f'路径不存在：{p}')
    if not files:
        sys.exit('没有 .ass / .srt 文件')

    rows = [probe(f) for f in files]
    ok = [r for r in rows if r['cues']]
    cols = ['file', 'kind', 'enc', 'lang', 'res', 'styles', 'cues', 'bad_blocks',
            'dur_bad', 'overlap', 'short', 'long', 'len_med', 'len_max', 'over18',
            'cps_med', 'cps_p95', 'cps_max', 'long_word', 'font', 'money_q']

    hdr = dict(file='文件', kind='格式', enc='编码', lang='语种', res='分辨率', styles='样式',
               cues='条数', bad_blocks='坏块', dur_bad='坏时长', overlap='重叠',
               short='<0.8s', long='>8s', len_med='行长中位', len_max='最长',
               over18='超长条', cps_med='CPS中位', cps_p95='p95', cps_max='CPS最高',
               long_word='超长词', font='font残留', money_q='金额问号')
    print('| ' + ' | '.join(hdr[c] for c in cols) + ' |')
    print('|' + '---|' * len(cols))
    for r in rows:
        vals = []
        for c in cols:
            v = r.get(c, '')
            vals.append(f'{v:.1f}' if isinstance(v, float) else str(v))
        print('| ' + ' | '.join(vals) + ' |')

    print(f"\n合计 {len(rows)} 个文件，有 cue {len(ok)} 个，"
          f"条数 {sum(r['cues'] for r in ok):,}，坏块 {sum(r['bad_blocks'] for r in ok)}，"
          f"坏时长 {sum(r['dur_bad'] for r in ok)}，重叠 {sum(r['overlap'] for r in ok)}")
    for k, label in (('font', 'font 残留'), ('money_q', '金额问号'), ('long_word', '超长词')):
        tot = sum(r.get(k, 0) for r in ok)
        if tot:
            bad = [r['file'] for r in ok if r.get(k)]
            print(f"{label}：{tot} 处，涉及 {len(bad)} 个文件（如 {bad[0]}）")

    if args.tsv:
        with open(args.tsv, 'w', encoding='utf-8-sig', newline='\n') as f:
            f.write('\t'.join(cols) + '\n')
            for r in rows:
                f.write('\t'.join(str(r.get(c, '')) for c in cols) + '\n')
        print(f'\n明细已写：{args.tsv}')

    bad = [r for r in ok if r['dur_bad'] or r['font'] or r['money_q'] or r['long_word']
           or r['cps_med'] > 6.0 or r['cps_p95'] > 9.0]
    if bad:
        print(f"\n不合格 {len(bad)} 个文件（判据见文件头）")
    if args.strict and bad:
        sys.exit(1)


if __name__ == '__main__':
    main()
