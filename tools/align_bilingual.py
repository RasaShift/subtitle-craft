#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-05（新建：跨剧通用双版本对齐，中文轨道当骨架、英文挂载）
"""align_bilingual.py -- 双版本字幕对齐（只读输入，配对结果写 TSV）

为什么有它：同一部剧的中文轨和英文轨是分别做的轴、切行不同，条号对不上，
不能按位置硬配。但两轨都来自同一条音轨，时间轴高度重叠——所以按时间配，
文本只当辅助校验，绝不当主判据（存在语序相反的句子对，按文本配会配错）。

输出纪律：**中文轨当骨架**，每一条中文挂 0 或 1 条英文，不拆不并；
挂不上的原样留一行、打标记交人工，不静默丢弃。

用法：
  python align_bilingual.py <中文.srt> <英文.srt> [--tsv out.tsv]
  python align_bilingual.py <目录> --zh-tag .zh --en-tag .en [--outdir 输出目录]

输出 TSV 列（UTF-8-SIG，制表符分隔）：
  中文条号 / 中文开始 / 中文结束 / 中文文本 / 英文条号 / 英文开始 / 英文结束 /
  英文文本 / 置信度 / 标记

置信度：1.00 起止全同 · 0.95 起点同 · 0.90 终点同 · 其余按重叠比折算
标记：空 = 正常 · 低置信 = 建议人工看一眼 · 未配对:无候选 = 该时段英文轨确实没有
     未配对:被占用 = 该时段英文轨有内容但已被别的中文条占用（多半是切行不同）

读这个 TSV 必须 `csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE)`：
台词常以引号开头（如 "透明政府"），csv 默认会把引号当「引号包裹字段」的定界符，
轻则啃掉引号、重则把后面几列吞进一个字段（2026-11-05 实测踩过）。

自检：配对后算「中文字数 vs 英文词数」的相关系数。同一集两轨的长度必然同步，
配错集则掉到 0 附近（实测同集 0.76 / 错集 -0.02），低于 --min-corr 提示疑似配错文件。

判据（--strict 触发非零退出，阈值由调用方给，不写死）：
  --max-unpaired N   未配对中文条超过 N 条即不合格
  --min-paired P     配对率低于 P（0~1）即不合格
  --min-corr C       文本长度相关系数低于 C 即不合格（不给则按 0.35 只提示不拦）

未配对不都算错，分四类（标记列直接写出来）：
  未配对:标识  = 标题卡/字幕组标。判据：带 font/i/b 标签、命中制作组词表（字幕组/翻译/
                  校对/时间轴/压制…）、或落在片头 --head-window / 片尾 30 秒内且文字很短。
                  这类本来就没有对应台词，不算漏
  未配对:注释  = 整条被括号包裹（如「(行政部)」），是剧中文字注释，不算漏
  未配对:无候选 = 该时段英文轨真的没有内容 —— 可能是漏判的卡，也可能是英文版本身缺这句
  未配对:被占用 = 该时段英文轨有内容但被别的中文条占走 —— 这是对齐器的错（切行不同）
「排除标题卡区后未挂载 = 0」就是数后两类，工具直接报 `真漏 N`。**别为了把这个数压到 0
去放宽规则**：短台词的真漏（「是」「不」）和四字栏目名卡在机器眼里一模一样，放宽就等于
放过真错。工具把判成卡的每一条都打印出来，人扫一眼，卡就是卡，台词就抓回来。
人工复核后确认某条是卡的，用 `--card <条号或文本>` 点名，标记进 TSV 写作
`未配对:标识(人工)`——留痕，且不动规则。
"""
import argparse
import pathlib
import re
import sys

SRT_TIME = re.compile(r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*'
                      r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})')
INDEX = re.compile(r'^\d+$')
CJK = re.compile(r'[\u4e00-\u9fff]')
WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
TAG = re.compile(r'</?(?:font|i|b|u)\b[^>]*>|\{[^}]*\}', re.I)
WRAP = re.compile(r'^[\(（\[［【][\s\S]*[\)）\]］】]$')
CREW = re.compile(r'字幕组|字幕制作|字幕翻译|翻译[:：]|校对[:：]|时间轴[:：]|压制[:：]|'
                  r'仅供交流|严禁商用|谢绝转载')
SHORT = 8          # 片头/片尾窗口内、中文字数少于这个数，按「标题卡」处理
HEAD_WINDOW = 90   # 片头窗口（秒）
TAIL_WINDOW = 30   # 片尾窗口（秒）


def decode(p):
    raw = p.read_bytes()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw.decode('utf-16')
    if raw[:2] == b'\xef\xbb\xbf':
        return raw.decode('utf-8-sig')
    if b'\x00' in raw[:8]:
        return raw.decode('utf-16-le')
    return raw.decode('utf-8-sig')


def to_ms(h, m, s, frac):
    # SRT 是毫秒 3 位；ASS 借道过来可能是 2 位（厘秒），按位数补
    return ((int(h) * 3600 + int(m) * 60 + int(s)) * 1000
            + int(frac.ljust(3, '0')[:3]))


def ms2tc(ms):
    ms = int(ms)
    return '%02d:%02d:%02d,%03d' % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def parse_srt(path):
    """解析 SRT。返回 [{no, s, e, text}]，s/e 为整数毫秒。"""
    cues, bad = [], 0
    for block in re.split(r'\r?\n\s*\r?\n', decode(path).strip()):
        lines = [x for x in block.splitlines() if x.strip()]
        if not lines:
            continue
        no, body_at, m = None, 0, None
        for i, ln in enumerate(lines[:3]):
            if INDEX.match(ln.strip()) and no is None:
                no = int(ln.strip())
            m = SRT_TIME.search(ln)
            if m:
                body_at = i + 1
                break
        if not m:
            bad += 1
            continue
        g = m.groups()
        cues.append({'no': no, 's': to_ms(*g[:4]), 'e': to_ms(*g[4:]),
                     'text': '\n'.join(lines[body_at:])})
    for i, c in enumerate(cues):  # 文件里没写序号时按位置补
        if c['no'] is None:
            c['no'] = i + 1
    cues.sort(key=lambda c: c['s'])
    return cues, bad


def reward(a, b, min_overlap):
    """a=中文条 b=英文条。用共同时长占较短一条的比例做基础分，起点靠近再给点加成。

    只用时间，不看文本：实测存在语序相反的对子（中文说「所以…之后」，英文说的是
    上一句的意思），按文本相似度配必错。
    """
    ov = min(a['e'], b['e']) - max(a['s'], b['s'])
    if ov <= 0:
        return None
    dmin = min(a['e'] - a['s'], b['e'] - b['s'])
    if dmin <= 0:
        return None
    r = ov / dmin
    if r < min_overlap:
        return None
    dmax = max(a['e'] - a['s'], b['e'] - b['s'], 1)
    bonus = 0.08 * max(0.0, 1.0 - abs(a['s'] - b['s']) / dmax)
    return r, min(1.0, r + bonus)


def align(zh, en, min_overlap=0.5):
    """单调保序最大权匹配（LCS 式 DP）。允许跳过任一侧，跳过得 0 分。"""
    n, m = len(zh), len(en)
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    # 回溯：0=跳过中文 1=跳过英文 2=配对
    bt = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        a = zh[i - 1]
        row, prev, brow = dp[i], dp[i - 1], bt[i]
        for j in range(1, m + 1):
            best, move = prev[j], 0
            if row[j - 1] > best:
                best, move = row[j - 1], 1
            rw = reward(a, en[j - 1], min_overlap)
            if rw is not None and prev[j - 1] + rw[1] > best:
                best, move = prev[j - 1] + rw[1], 2
            row[j] = best
            brow[j] = move

    pairs = {}
    i, j = n, m
    while i > 0 and j > 0:
        move = bt[i][j]
        if move == 2:
            pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif move == 0:
            i -= 1
        else:
            j -= 1
    return pairs


def confidence(a, b, min_overlap):
    if abs(a['s'] - b['s']) <= 5 and abs(a['e'] - b['e']) <= 5:
        return '1.00'
    if abs(a['s'] - b['s']) <= 5:
        return '0.95'
    if abs(a['e'] - b['e']) <= 5:
        return '0.90'
    rw = reward(a, b, min_overlap)
    ov = rw[0] if rw else 0.0
    return '%.2f' % (0.50 + 0.40 * ov)


def kind_of(a, head_window, tail_window):
    """未配对的中文条属于哪一类。顺序要紧：被占用优先，别把真漏当成标题卡放过。"""
    txt = a['text'].strip()
    plain = TAG.sub('', txt).replace('"', '').replace('“', '').replace('”', '').strip()
    cjk = len(CJK.findall(plain))
    if WRAP.match(plain):
        return '未配对:注释'
    if TAG.search(txt):
        return '未配对:标识'
    if CREW.search(plain) and cjk < 40:
        return '未配对:标识'
    if cjk < SHORT and (a['s'] < head_window or a['e'] > tail_window):
        return '未配对:标识'
    return '未配对:无候选'


def is_card(a, cards):
    """人工点名「这条是卡」。cards 每项可以是中文条号，也可以是文本片段。
    这是人工复核的出口：不把某部剧的栏目名硬写进词表，也不为它放宽字数阈值。"""
    for c in cards or ():
        if c.isdigit() and int(c) == a['no']:
            return True
        if not c.isdigit() and c in a['text']:
            return True
    return False


def run(zh, en, min_overlap=0.5, head_window=HEAD_WINDOW * 1000,
        tail_window=None, cards=None):
    if tail_window is None:
        tail_window = max((c['e'] for c in zh), default=0) - TAIL_WINDOW * 1000
    pairs = align(zh, en, min_overlap)
    used = set(pairs.values())
    rows = []
    for i, a in enumerate(zh):
        if i in pairs:
            b = en[pairs[i]]
            rows.append({'zh': a, 'en': b, 'conf': confidence(a, b, min_overlap), 'flag': ''})
        else:
            # 先看是不是「有内容但被抢了」，这类是真漏，不能当标识放过
            if any(reward(a, b, min_overlap) for b in en):
                flag = '未配对:被占用'
            elif is_card(a, cards):
                flag = '未配对:标识(人工)'
            else:
                flag = kind_of(a, head_window, tail_window)
            rows.append({'zh': a, 'en': None, 'conf': '0.00', 'flag': flag})
    for r in rows:
        if r['en'] is not None and not r['flag'] and float(r['conf']) < 0.70:
            r['flag'] = '低置信'
    return rows, used, pairs


HEAD = ['中文条号', '中文开始', '中文结束', '中文文本', '英文条号', '英文开始',
        '英文结束', '英文文本', '置信度', '标记']


def cell(s):
    return ' '.join(s.split()) if s else ''


def write_tsv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8-sig', newline='\n') as f:
        f.write('\t'.join(HEAD) + '\n')
        for r in rows:
            a, b = r['zh'], r['en']
            f.write('\t'.join([
                str(a['no']), ms2tc(a['s']), ms2tc(a['e']), cell(a['text']),
                str(b['no']) if b else '', ms2tc(b['s']) if b else '',
                ms2tc(b['e']) if b else '', cell(b['text']) if b else '',
                r['conf'], r['flag']]) + '\n')


def text_corr(rows):
    """中文字数 vs 英文词数的相关系数。同一集两轨必然同步；配错集掉到 0 附近。"""
    xs, ys = [], []
    for r in rows:
        if r['en'] is None:
            continue
        xs.append(len(CJK.findall(r['zh']['text'])))
        ys.append(len(WORD.findall(r['en']['text'])))
    n = len(xs)
    if n < 10:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    vx = sum((a - mx) ** 2 for a in xs) ** 0.5
    vy = sum((b - my) ** 2 for b in ys) ** 0.5
    if not vx or not vy:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / (vx * vy)


def summarize(name, rows, zh_n, en_n, head_window):
    paired = sum(1 for r in rows if r['en'] is not None)
    none = sum(1 for r in rows if r['flag'] == '未配对:无候选')
    taken = sum(1 for r in rows if r['flag'] == '未配对:被占用')
    ident = sum(1 for r in rows if r['flag'].startswith('未配对:标识'))
    note = sum(1 for r in rows if r['flag'] == '未配对:注释')
    low = sum(1 for r in rows if r['flag'] == '低置信')
    rate = paired / zh_n if zh_n else 0.0
    print(f'{name}: 中文 {zh_n} 条 / 英文 {en_n} 条 / 配对 {paired} 条 ({rate:.1%})')
    print(f'  未配对 {none + taken + ident + note} 条 = 标识 {ident} + 注释 {note} + '
          f'无候选 {none} + 被占用 {taken}　→ 排除标题卡区后真漏 {none + taken} 条')
    if low:
        print(f'  低置信 {low} 条，建议人工扫')
    shown = 0
    for r in rows:
        if (r['flag'].startswith('未配对:标识') or r['flag'] == '未配对:注释') and shown < 12:
            shown += 1
            mark = '人工' if r['flag'].endswith('(人工)') else '机器'
            print(f'  [卡·{mark}] #{r["zh"]["no"]} {ms2tc(r["zh"]["s"])} '
                  f'{" ".join(r["zh"]["text"].split())[:38]}')
    if shown:
        print(f'  以上 {shown} 条按标题卡/组标/注释处理，请扫一眼有没有漏译的台词')
    return {'zh': zh_n, 'en': en_n, 'paired': paired, 'none': none, 'taken': taken,
            'ident': ident, 'note': note, 'low': low, 'rate': rate,
            'real_miss': none + taken, 'corr': text_corr(rows)}


def one(zh_path, en_path, tsv_path, min_overlap, min_corr, head_window, cards=None):
    zh, bad_zh = parse_srt(zh_path)
    en, bad_en = parse_srt(en_path)
    if not zh or not en:
        sys.exit(f'解析不出内容：{zh_path if not zh else en_path}')
    if bad_zh or bad_en:
        print(f'[警告] 坏块 中文 {bad_zh} / 英文 {bad_en}，已跳过')
    rows, used, pairs = run(zh, en, min_overlap, head_window, cards=cards)
    if tsv_path:
        write_tsv(tsv_path, rows)
    s = summarize(zh_path.name, rows, len(zh), len(en), head_window)
    if s['corr'] is not None:
        warn_at = 0.35 if min_corr is None else min_corr
        print(f'  文本长度相关 r={s["corr"]:.2f}', end='')
        print('  [警告] 偏低，两份文件疑似不是同一集/同一版' if s['corr'] < warn_at else '')
    s['unused_en'] = len(en) - len(used)
    if s['unused_en']:
        print(f'  另有 {s["unused_en"]} 条英文没被挂上（英文比中文多，或切行不同）')
    if tsv_path:
        print(f'  配对表：{tsv_path}')
    return s


def main():
    ap = argparse.ArgumentParser(description='双版本字幕对齐：中文当骨架，英文挂 0/1 条')
    ap.add_argument('zh', help='中文 SRT 路径，或批量模式下的目录')
    ap.add_argument('en', nargs='?', help='英文 SRT 路径')
    ap.add_argument('--tsv', help='配对表输出路径（批量模式下为输出目录）')
    ap.add_argument('--zh-tag', default='.zh', help='批量模式：中文文件后缀标记')
    ap.add_argument('--en-tag', default='.en', help='批量模式：英文文件后缀标记')
    ap.add_argument('--min-overlap', type=float, default=0.5,
                    help='共同时长占较短一条的最低比例，低于此不配（默认 0.5）')
    ap.add_argument('--min-paired', type=float, help='配对率低于此值即不合格')
    ap.add_argument('--max-unpaired', type=int,
                    help='排除标题卡/注释后，真漏中文条超过此数即不合格')
    ap.add_argument('--head-window', type=float, default=HEAD_WINDOW,
                    help=f'片头窗口秒数，窗口内短句未配对按标题卡处理（默认 {HEAD_WINDOW}）')
    ap.add_argument('--card', action='append', metavar='条号或文本',
                    help='人工确认「这条是卡」，可给中文条号或文本片段，可重复。'
                         '只用于单集模式（条号是每集各自的）')
    ap.add_argument('--min-corr', type=float,
                    help='文本长度相关系数低于此值判不合格；不给则按 0.35 只提示不拦')
    ap.add_argument('--strict', action='store_true', help='触发判据时非零退出')
    args = ap.parse_args()

    root = pathlib.Path(args.zh)
    results = []
    head_ms = int(args.head_window * 1000)
    if root.is_dir():
        if args.card:
            print('[提示] 批量模式下 --card 不生效（条号是每集各自的，容易认错集）；'
                  '要标卡请单集跑一次')
        outdir = pathlib.Path(args.tsv) if args.tsv else None
        ens = {p.name[: -len(args.en_tag) - 4]: p
               for p in root.glob(f'*{args.en_tag}.srt')}
        zhs = sorted(root.glob(f'*{args.zh_tag}.srt'), key=lambda p: p.name)
        if not zhs:
            sys.exit(f'{root} 下没有 *{args.zh_tag}.srt')
        for zp in zhs:
            stem = zp.name[: -len(args.zh_tag) - 4]
            ep = ens.get(stem)
            if ep is None:
                print(f'{stem}: 缺英文轨，跳过')
                continue
            tp = (outdir / f'{stem}.align.tsv') if outdir else None
            results.append(one(zp, ep, tp, args.min_overlap, args.min_corr, head_ms))
    else:
        if not args.en:
            sys.exit('给了中文轨但没给英文轨')
        results.append(one(root, pathlib.Path(args.en),
                           pathlib.Path(args.tsv) if args.tsv else None,
                           args.min_overlap, args.min_corr, head_ms, args.card))

    if not results:
        sys.exit('没有可跑的文件对')

    if len(results) > 1:
        tot = sum(r['paired'] for r in results)
        n = sum(r['zh'] for r in results)
        print(f'\n合计 {len(results)} 对：中文 {n} 条，配对 {tot} 条（{tot / n:.1%}），'
              f'标识 {sum(r["ident"] for r in results)}，'
              f'注释 {sum(r["note"] for r in results)}，'
              f'真漏 {sum(r["real_miss"] for r in results)}，'
              f'低置信 {sum(r["low"] for r in results)}')

    bad = []
    if args.max_unpaired is not None:
        for r in results:
            if r['real_miss'] > args.max_unpaired:
                bad.append(f'真漏 {r["real_miss"]} 条 > {args.max_unpaired}')
                break
    if args.min_paired is not None and any(r['rate'] < args.min_paired for r in results):
        worst = min(r['rate'] for r in results)
        bad.append(f'配对率最低 {worst:.1%} < {args.min_paired:.0%}')
    if args.min_corr is not None:
        lowc = [r['corr'] for r in results if r['corr'] is not None and r['corr'] < args.min_corr]
        if lowc:
            bad.append(f'文本长度相关最低 {min(lowc):.2f} < {args.min_corr}（疑似配错文件）')
    if bad:
        print('\n不合格：' + '；'.join(bad))
        if args.strict:
            sys.exit(1)


if __name__ == '__main__':
    main()
