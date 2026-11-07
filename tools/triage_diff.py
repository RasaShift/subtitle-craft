#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-05（新建；同日重写数字归一化：中文按亿/万分节、参照认点号千分位与量级词，
#                        9 集假红 21 条 → 修完 4 条）
"""triage_diff.py -- 对稿（只读配对表，出候选清单）

判据全在 `docs/工序-对稿.md`，本脚本只实现其中「机器能判」的那部分。
一句话分工：**脚本挑嫌疑，人定案。** 语义是「意思对不对」，脚本判不了。

为什么只出候选：把语义判断交给规则，唯一的结果是把「看着像错、其实不错」判成错，
或者反过来整类放过。所以这里每条都带「规则名 + 证据」，人一眼能复核。

用法：
  python triage_diff.py <配对表.tsv> [--term 词表.tsv] [--tsv 输出.tsv]
  python triage_diff.py 对齐表/YM_S01E01.align.tsv --tsv 分诊/YM_S01E01.triage.tsv

输入是 `align_bilingual.py` 出的配对表。读法必须 quoting=csv.QUOTE_NONE——
台词常以引号开头，csv 默认会把引号当字段定界符，啃掉引号甚至吞掉后面几列。

输出列：中文条号 / 中文开始 / 中文文本 / 参照文本 / 建议代号 / 规则 / 理由 / 证据 / 需人判 / stage
建议代号取 T1 漏译 / T2 误译 / T? 无法判定；只输出被规则挑中的行。`stage` 恒为 `triage`。

统计报一行：
  T1 嫌疑 a / T2 嫌疑 b / T? c / 未标记 d（未标记默认按 T3 表述差异，需抽样复核）

规矩（来自工序文档第 8 节）：
  · 不许为了把数字压小去放宽规则；
  · 拿不准落 T?，硬判才是错；
  · 参照方自己听错的，不构成我方该改的理由。
"""
import argparse
import csv
import pathlib
import re
import sys

CJK = re.compile(r'[\u4e00-\u9fff]')
LAT = re.compile(r"[A-Za-z][A-Za-z'\-]*")
# 中文数字：允许「2万1千793」「3千2百万」「20万」这种汉字单位混写，也允许小数
NUMZH = re.compile(r'\d[\d,]*(?:\.\d+)?(?:[亿万千百十]\d*)*')
# 参照侧数字：带可选量级词。2.000 / 23.000 是点号千分位，7.45 / 7:45 是时间或小数
NUMREF = re.compile(r'(\d[\d,]*(?:[.:]\d+)?)\s*(hundred|thousand|million|billion)?', re.I)
# 中文时间写法「7点45」= 7:45，先并成一个数再比，否则会和参照的 7.45 对不上
CN_TIME = re.compile(r'(\d+)\s*点\s*(\d+)')
SCALE = {'hundred': 10 ** 2, 'thousand': 10 ** 3,
         'million': 10 ** 6, 'billion': 10 ** 9}
CN_UNIT = {'十': 10, '百': 100, '千': 1000}
CN_SECT = (('亿', 10 ** 8), ('万', 10 ** 4))    # 从大到小，先分节再算节内
SDH = re.compile(r'[\[［][^\]］]*[\]］]|♪|♫')
ELLIPSIS = re.compile(r'(\.\.\.|…)\s*$')


def read_pairs(path):
    """读配对表。QUOTE_NONE 是硬要求，见文件头。"""
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f, delimiter='\t', quoting=csv.QUOTE_NONE))
    if not rows:
        sys.exit(f'配对表是空的：{path}')
    need = {'中文条号', '中文文本', '英文文本'}
    if not need <= set(rows[0]):
        sys.exit(f'配对表缺列：需要 {sorted(need)}，实际 {sorted(rows[0])}')
    return rows


def read_terms(path):
    """词表：两列，第一列原文（参照里的写法），第二列规定译法。没有表就返回空。"""
    if not path:
        return []
    out = []
    with open(path, encoding='utf-8-sig', newline='') as f:
        for i, line in enumerate(f):
            parts = [x.strip() for x in line.rstrip('\n').split('\t')]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                continue
            if i == 0 and ('原文' in parts[0] or 'source' in parts[0].lower()):
                continue  # 表头
            out.append((parts[0], parts[1]))
    return out


def _cn_inner(s):
    """节内累加：'3千2百'→3200、'793'→793、''→0。只认十/百/千，不认万亿。"""
    total, cur = 0, None
    for piece in re.findall(r'\d+|[十百千]', s):
        if piece in CN_UNIT:
            total += (cur or 0) * CN_UNIT[piece]
            cur = None
        else:
            cur = int(piece.replace(',', ''))
    return total + (cur or 0)


def cn_value(tok):
    """中文数字归一：'2万1千793'→21793、'3千2百万'→32000000、'20万'→200000。

    必须按「亿/万」分节再累加节内，不能一律相加——
    '3千2百万' 是「3200 个万」= 3200 万，不是 3000+200+万（旧版算成 3002，
    9 集实测一次性误报 8 条）。
    """
    s = tok.replace(',', '')
    if not re.search(r'[亿万千百十]', s):
        return s.rstrip('.')
    total = 0
    for sect, mul in CN_SECT:
        if sect in s:
            left, _, s = s.partition(sect)
            total += _cn_inner(left) * mul
    return str(total + _cn_inner(s))


def _scale_int(s, scale):
    s = s.strip(',.')
    return str(int(s) * scale) if s.isdigit() else s


def ref_readings(tok, unit=''):
    """参照侧一个数字的**全部合理读法**。读法给不准，就给全，别猜。

    三种点号必须分开处理，混一起就是满屏假红（9 集实测误报 15 条）：
    · 2.000 / 23.000 —— 点号当千分位（欧洲写法）
    · 10.00 / 5.00   —— 尾部全零，按整数读（时间点）
    · 7.45 / 9.75    —— 既可能是 7:45，也可能是 7.45，两种都给
    再乘量级词：32 million → 32000000。
    """
    s = tok.replace(',', '')
    scale = SCALE.get((unit or '').lower(), 1)
    out = set()
    if ':' in s:                                    # 7:45 是时间，1:10 是比例，两读都给
        out.add(_scale_int(s.replace(':', ''), scale))
        out.update(p for p in s.split(':') if p.isdigit())
    elif '.' in s:
        ip, _, fp = s.partition('.')
        if len(fp) == 3 and ip:                     # 2.000 → 2000
            out.add(_scale_int(ip + fp, scale))
        elif set(fp) == {'0'}:                      # 10.00 → 10
            out.add(_scale_int(ip, scale))
        else:
            v = float(s) * scale
            out.add(str(int(v)) if v == int(v) else f'{v:g}')
            out.add(_scale_int(ip + fp, scale))     # 7.45 → 745（时间读法）
    else:
        out.add(_scale_int(s, scale))
    return out


def nums_of(text):
    """中文侧数字。先把「7点45」并成 745，再按亿/万归一。"""
    return {cn_value(t) for t in NUMZH.findall(CN_TIME.sub(r'\1\2', text or ''))}


def missing_nums(zh, ref):
    """参照有、中文没有的数。任一读法对得上就算有——只查这个方向。"""
    zh_v = nums_of(zh)
    if not zh_v:
        return []
    miss = []
    for m in NUMREF.finditer(ref or ''):
        reads = ref_readings(m.group(1), m.group(2))
        if not (reads & zh_v):
            miss.append(sorted(reads)[0])
    return miss


def cjk_n(text):
    return len(CJK.findall(text or ''))


def words_of(text):
    return LAT.findall(text or '')


def ref_word_n(text):
    """参照词数：**单个大写字母不算词**（那是缩写拆出来的）。

    "T.P.L.A.C." 会被 LAT 拆成 5 个「词」，把比例的分母撑大，让正常句算出 0.43
    （9 集实测误报 1 条）。只排大写单字母，不排 "a" / "I"——那两个是真词，
    排掉会把「没着凉吧?」对 "Are you going to see a doctor?" 这类真错位也一起放过。
    """
    return sum(1 for w in words_of(text) if not (len(w) == 1 and w.isupper()))


def judge(zh, ref, terms, mark=''):
    """返回 (代号, 规则, 理由) 或 (None, None, None)（不挑）。顺序固定：先问有没有(T1)，再问对不对(T2)。"""
    zh = (zh or '').strip()
    ref = (ref or '').strip()

    # 对齐环节已判成标题卡/组标/注释的，不是对白，不进分诊
    if mark:
        if mark.startswith('未配对:标识') or mark == '未配对:注释':
            return None, None, None
        if mark in ('未配对:无候选', '未配对:被占用'):
            return 'T?', 'DR0 参照侧空', f'对齐结果标记「{mark}」，该时段参照没有内容'

    # 边界：整条只有 SDH 音效/歌词标记，不算漏译，判了就是假红
    if SDH.search(zh) and cjk_n(SDH.sub('', zh)) == 0:
        return None, None, None

    if not ref:
        return 'T?', 'DR0 参照侧空', '这一条没有参照文本，判不了'

    ref_words = ref_word_n(ref)
    zh_n = cjk_n(zh)

    # 中文这条只剩数字/标点（倒计时 5 4 3 2、页码）——既不是「没译」，也不该走比例。
    # 旧版把它判成 T1「整条未译」，9 集实测误报 1 条。
    if zh_n == 0 and not re.sub(r'[\d\s\W_]', '', zh):
        return None, None, None

    # T1：整条没有一个汉字（中文轨里塞的是原文）——没译，不是「待判」
    if zh_n == 0 and ref_words >= 2:
        return 'T1', 'DR2 整条未译', f'中文轨这条没有汉字，参照 {ref_words} 个词'

    # T1 嫌疑：中英比例掉下来。中文约 1.2–1.8 字/词，掉到 0.6 以下才报。
    # 阈值取保守值：0.8 会把「你要发表讲话」(6 字对 8 词) 这种正常句也报出来（实测假红）。
    # 不用绝对字数：8 个英文词译成 4 个汉字本来正常，拿字数当尺子会满屏假红（第一版实测）。
    if ref_words >= 6 and zh_n / ref_words < 0.6:
        return ('T1', 'DR3 比例过低',
                f'中文 {zh_n} 字对参照 {ref_words} 词（{zh_n / ref_words:.2f} 字/词），疑似漏句')

    # T2 嫌疑：参照里的数字在中文里找不到。只查这个方向——
    # 中文多出来的数字常常是「5月」对 "May"、「两万」对 "20,000" 这种写法差异，查了全是假红。
    # 反过来（参照有、中文没有）才说明真对不上：实测「卷 497」对中文写 487 是真错，会被抓到。
    a, miss = nums_of(zh), missing_nums(zh, ref)
    missing = sorted(set(miss))
    if a and missing:
        return 'T2', 'DR1 数字不一致', f'参照的 {"/".join(missing[:3])} 在中文里找不到'

    # T2 嫌疑：一整段没翻。单个专名（BBC / PM）不算——专名保留原文是正常译法，
    # 要 3 个以上拉丁词才说明这一段没翻。全大写缩写（CMG / KCMG）同样不算：
    # 那是勋章、机构代号，字幕里本来就不译（9 集实测误报 1 条）。
    left = [w for w in words_of(zh)
            if len(w) >= 3 and not (w.isupper() and len(w) <= 6)]
    if len(left) >= 3:
        return 'T2', 'DR4 拉丁残留', f'中文里 {len(left)} 个未译词：' + '/'.join(left[:3])

    # T2 嫌疑：术语没按词表。**词边界 + 最长优先**，两条缺一不可：
    # 少了词边界，`Frank` 会认成 `frankly` 的一部分、`Vic` 认成 `Service` 中间那三个字母；
    # 少了最长优先，`Prime Minister` 里的 `Minister` 会被单独拎出来，中文明明写「首相」也报红。
    # 2026-11-07 首次接上真词表，这两条一起发作，538 条里报出 31 条假红。
    spans = []
    for src, canon in sorted(terms, key=lambda x: -len(x[0])):
        for m in re.finditer(r'(?<![A-Za-z])' + re.escape(src) + r'(?![A-Za-z])', ref):
            a, b = m.start(), m.end()
            if any(a < y and x < b for x, y in spans):
                continue                      # 已落在更长的术语里，不重复报
            spans.append((a, b))
            if canon not in zh:
                return 'T2', 'DR5 术语不符', f'参照有「{src}」，应按词表译作「{canon}」'

    return None, None, None


HEAD = ['中文条号', '中文开始', '中文文本', '参照文本', '建议代号', '规则', '理由', '证据', '需人判', 'stage']


def read_offsets(path):
    """读人划的错位区间表 → [(起条号, 止条号, 理由)]。

    三列：起条号 / 止条号 / 理由。区间内的对子**整段中英对照本身就是错位的**
    （上一句的中文配了下一句的英文），逐条比只会把每一条都报成误译——所以跳过，不是判「没问题」。
    为什么人划不机划：要判「这一段对不上」得整段读一眼，机器的置信度阈值在本库有过前车之鉴
    （中英比例 0.6 那条，两部剧分布几乎重叠，怎么调都是拿真漏换假红）。
    """
    if not path:
        return []
    spans = []
    for i, line in enumerate(pathlib.Path(path).read_text(encoding='utf-8-sig').splitlines()):
        if not line.strip():
            continue
        parts = [x.strip() for x in line.split('\t')]
        if not parts[0].isdigit():
            if i == 0:
                continue                                  # 表头
            print(f'错位区间表第 {i + 1} 行首列不是条号：{line[:40]}', file=sys.stderr)
            sys.exit(2)
        if len(parts) < 2 or not parts[1].isdigit():
            print(f'错位区间表第 {i + 1} 行要三列（起条号 / 止条号 / 理由）：{line[:40]}', file=sys.stderr)
            sys.exit(2)
        a, b = int(parts[0]), int(parts[1])
        if b < a:
            print(f'错位区间表第 {i + 1} 行止条号小于起条号：{a}–{b}', file=sys.stderr)
            sys.exit(2)
        spans.append((a, b, parts[2] if len(parts) > 2 else ''))
    return spans


def in_span(no, spans):
    try:
        n = int(no)
    except (TypeError, ValueError):
        return None
    for a, b, why in spans:
        if a <= n <= b:
            return why
    return None


def cell(s):
    return ' '.join((s or '').split())


def main():
    ap = argparse.ArgumentParser(description='对稿：从配对表挑出嫌疑条目（不给定案）')
    ap.add_argument('pairs', help='align_bilingual.py 出的配对表 TSV')
    ap.add_argument('--term', help='词表 TSV（两列：参照写法 / 规定译法），可选')
    ap.add_argument('--offset', help='人划的错位区间表（三列：起条号 / 止条号 / 理由），区间内跳过不判')
    ap.add_argument('--tsv', help='输出路径')
    args = ap.parse_args()

    rows = read_pairs(pathlib.Path(args.pairs))
    terms = read_terms(args.term)
    spans = read_offsets(args.offset)

    cand, unmarked, skipped = [], 0, 0
    for r in rows:
        zh, ref = r.get('中文文本', ''), r.get('英文文本', '')
        if in_span(r.get('中文条号', ''), spans) is not None:
            skipped += 1
            continue
        code, rule, why = judge(zh, ref, terms, r.get('标记', ''))
        if not code:
            unmarked += 1
            continue
        cand.append({
            '中文条号': r.get('中文条号', ''), '中文开始': r.get('中文开始', ''),
            '中文文本': cell(zh), '参照文本': cell(ref),
            '建议代号': code, '规则': rule, '理由': why,
            '证据': (cell(ref) or cell(zh))[:60],
            '需人判': '是', 'stage': 'triage',
        })

    cand.sort(key=lambda x: int(x['中文条号']) if x['中文条号'].isdigit() else 0)

    t1 = sum(1 for c in cand if c['建议代号'] == 'T1')
    t2 = sum(1 for c in cand if c['建议代号'] == 'T2')
    tq = sum(1 for c in cand if c['建议代号'] == 'T?')
    src = pathlib.Path(args.pairs).name
    print(f'{src}: 共 {len(rows)} 条')
    print(f'  T1 嫌疑 {t1} / T2 嫌疑 {t2} / T? {tq} / 未标记 {unmarked}'
          f'（未标记默认按 T3 表述差异，需抽样复核）')
    if skipped:
        print(f'  跳过错位区间 {skipped} 条（人划的区间，中英对照本身错位，逐条比必假红）——'
              f'这几条不判不等于没问题，审稿要按区间整段读')
    if not terms:
        print('  [提示] 没给词表，DR5 术语检查没跑——这条不跑不代表没问题')

    if args.tsv:
        out = pathlib.Path(args.tsv)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, 'w', encoding='utf-8-sig', newline='\n') as f:
            f.write('\t'.join(HEAD) + '\n')
            for c in cand:
                f.write('\t'.join(str(c[h]) for h in HEAD) + '\n')
        print(f'  候选清单：{out}')


if __name__ == '__main__':
    main()
