#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-06（加补行：增行按时间轴定位，after_no 只作核对；verify 改按「期望序列」对位）
"""land_verify.py -- 落地（apply）与复算（verify）：把改动写进成品，再证明只改了该改的

为什么要有它：改完字幕最难证明的不是「改对了」，是「**没多改**」。
手工改一百条，事后没人能说清是不是顺手碰到了别处。分工固定：判 = 分诊/通读/查表，
写和验 = 本脚本，但**不放在一条命令里替人打包票**——apply 和 verify 是两条独立命令。

管三件事：① 换字（按「条号 + 旧文本」双锚点，只换中文段）；② 改时间码（按「条号 + 旧时间码」
双锚点，只改声明的那两条时间）；③ 补行（整句漏译补一条新行）。
三样都不动英文段、不动未声明的行、不动没声明的时间码。

用法：
    python land_verify.py apply  <成品.ass> <改动.tsv> [--inserts 补行.tsv] [-o 输出] [--in-place] [--dry-run]
    python land_verify.py verify <改前> <改后> <改动.tsv> [--inserts 补行.tsv]

改动.tsv 三列必需（带不带表头都行）：`no` / `old` / `new`，即 条号 / 旧文本 / 新文本
  · **可选列按表头认名**：`old_time` / `new_time`（成对出现，各自写 `开始-结束`，如
    `0:11:41.12-0:11:42.12`）、`stage`（`mech` / `triage` / `read` / `fmt`，本脚本只透传，
    验收拿它把密度分两类）。**没有表头时只认前三列**——第四列起可能是 `rule` / `note` /
    `evidence`，按下标硬猜会把规则名当时间码。
  · 时间码也走双锚点：`old_time` 必须与该条改前的时间码一致，不一致按失配处理（不改，不猜）。
    声明了 `old_time` 却不给 `new_time`（或反过来）直接拒。**只支持 ASS**——SRT 的时间码在
    `-->` 那一行，改它等于改块结构，本版不做。
  · 旧文本**等于该条整句**时按整条替换；否则按子串替换，且必须恰好命中 1 次。
    命中 0 次或多次都算锚点失配——多次就是不唯一，不替人决定改哪一个。
  · **同一条号只许出现一行**。两行改同一条会被后一行覆盖前一行，而 verify 按条号去重后
    仍然全绿（2026-11-05 实测：apply 报「应改 2 实改 2」、verify 报「声明 1 / 在位 1 /
    多改 0」、退出码 0，前一条的改动静默消失）。同条多处改动先合成一行再落地。

补行.tsv 六列（带不带表头都行）：`start` / `end` / `cn` / `en` / `after_no` / `style`
  · **插在哪由 `start` 算，不由 `after_no` 定**。`after_no` 填了就核对：工具按时间算出的
    锚点与它不一致 → 退出码 2。理由：老库 `after_ln` 只管位置不管时间，出过「19:31 后面
    跟着 19:06」的跳行（`日常杂活/procedures/字幕管线-补行要先核时间轴.md`）；而且 verify
    是独立命令、不信任 apply，只有位置算法确定性，它才能自己重算一遍得到同一个答案。
  · 时间不许和相邻条重叠：`start` 落在锚点条显示区间内、或 `end` 超过下一条 `start`，都拒。
  · 新行前缀（Layer/Style/Actor/边距/Effect）抄锚点条；`style` 给了就覆盖——歌词补行要用
    歌词长样式，这条是出口。
  · `cn` / `en` 里的换行写 `\\N`（ASS 原样，不转义）。`en` 留空 = 只补中文。
  · **`after_no` 是条号，不是文件行号。** 老库 `inserts` 里的 `after_ln` 是**文件行号**
    （同一集同一位置，两个数差 19——文件头占 19 行，差多少随头部行数变）。从老库迁移补行
    时要么换算成条号，要么留空让它按时间算；直接照抄会静默插错位置，而 verify 用的是同一套
    算法，会跟着一起错。
  · **只支持 ASS**。SRT 是块结构（序号要重排），补行不在本版范围，遇到直接拒。

落地纪律：
  · 默认不原地改，写 `<原名>.fixed.<ext>`；`--in-place` 才原地，且先存 `.bak`
  · 写完立刻自复算，不过就退出码 1——「写了」和「写对了」是两件事
  · 行数变动只认「原行数 + 声明补行数」，多一行少一行都算错
  · 声明里没提到的条必须一字未变——复算逐条比，这是「没多改」的唯一证据

退出码：0 成功；1 复算不过；2 参数/输入错（含补行位置对不上）；3 dry-run 没有可改项
"""
import argparse
import re
import shutil
import sys
from pathlib import Path

from align_bilingual import decode
from bilingual_pairs import split_bilingual

ASS_FIELD = 9                       # Dialogue 行的 Text 是第 10 个字段
EN_MARK = '\\N{\\rEnglish}'
CJK = re.compile(r'[\u4e00-\u9fff]')
# 改动表的可选列：按表头名认，不按下标猜。中文表头与英文表头都收——
# 本库自己写中文表头（条号/旧文本/新文本），findings2tsv 吐的是英文名。
CHANGE_COLS = {
    'no': 'no', '条号': 'no', '中文条号': 'no',
    'old': 'old', '旧文本': 'old', '中文文本': 'old',
    'new': 'new', '新文本': 'new',
    'old_time': 'old_time', '旧时间码': 'old_time',
    'new_time': 'new_time', '新时间码': 'new_time',
    'stage': 'stage', '层': 'stage',
}
SYNC = re.compile(r'^\d{1,2}:\d{2}:\d{2}[,.]\d{1,3}\s*-->')
SRT_TC = re.compile(r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*'
                    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})')


def to_ms(t):
    """时间码 → 毫秒。ASS 写 `0:00:40.30`（2 位厘秒），SRT 写 `00:00:40,300`，两种都认。

    解析只用来算位置和查重叠，写回时用声明里的原字符串——不做格式化往返，
    因为 `0:00:40.3` 和 `0:00:40.30` 在 ASS 里是同一个时间，往返一次就白白改了整份文件。
    """
    s = str(t).strip()
    m = re.match(r'^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})$', s)
    if not m:
        return None
    h, mi, sec, frac = m.groups()
    ms = int(frac) * 10 ** (3 - len(frac))     # 厘秒 52 → 520ms，不是 52ms
    return (int(h) * 3600 + int(mi) * 60 + int(sec)) * 1000 + ms



def plain(s):
    return ' '.join(re.sub(r'\{[^}]*\}', '', s).split())


def load(path):
    """读成「原样行 + 每条的位置和原文」。只记位置，不改内容。

    返回 (lines, cues, kind)。lines 保留原换行符，写完能逐行比。
    """
    text = decode(path)
    lines = text.splitlines(keepends=True)
    bare = [ln.rstrip('\r\n') for ln in lines]
    kind = 'ass' if path.suffix.lower() == '.ass' else 'srt'
    # 后缀不可靠：备份惯用 .bak，工具自己复制出来的也未必带 .ass——有 Dialogue 行就是 ASS
    if kind == 'srt' and any(ln.lower().startswith('dialogue:') for ln in bare):
        kind = 'ass'
    cues = []

    if kind == 'ass':
        for i, ln in enumerate(bare):
            if not ln.lower().startswith('dialogue:'):
                continue
            parts = ln.split(',', ASS_FIELD)
            if len(parts) <= ASS_FIELD:
                continue
            tf = parts[ASS_FIELD]
            zh, en = split_bilingual(tf)
            cues.append({'no': len(cues) + 1, 'line': i, 'prefix': ','.join(parts[:ASS_FIELD]) + ',',
                         'text': tf, 'zh': zh, 'en': en, 'body': [i],
                         's': to_ms(parts[1]), 'e': to_ms(parts[2])})
        return lines, cues, kind

    blocks, cur = [], []
    for i, ln in enumerate(bare):
        if ln.strip():
            cur.append(i)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    for b in blocks:
        ti = next((i for i in b if SYNC.match(bare[i])), None)
        if ti is None:
            continue
        body = [i for i in b if i > ti]
        zh, en = split_bilingual('\n'.join(bare[i] for i in body))
        m = SRT_TC.match(bare[ti].strip())
        cues.append({'no': len(cues) + 1, 'line': b[0], 'prefix': None, 'text': None,
                     'zh': zh, 'en': en, 'body': body, 'time': ti,
                     's': to_ms(m.group(1) + ':' + m.group(2) + ':' + m.group(3) + ',' + m.group(4)) if m else None,
                     'e': to_ms(m.group(5) + ':' + m.group(6) + ':' + m.group(7) + ',' + m.group(8)) if m else None})
    return lines, cues, kind


def split_pair(v):
    """`开始-结束` → ((s_ms, e_ms), (s原串, e原串))；认不出返回 None。

    两侧都要写全：只写一半时，另一半是「不变」还是「填漏了」机器猜不出来，
    而猜错的代价是把一条字幕的显示时长悄悄改掉。
    """
    parts = [x.strip() for x in str(v).split('-')]
    if len(parts) != 2:
        return None
    s, e = to_ms(parts[0]), to_ms(parts[1])
    if s is None or e is None or e <= s:
        return None
    return (s, e), (parts[0], parts[1])


def read_changes(path):
    """读改动表 → [{'no','old','new','old_t','new_t','stage','row'}]。

    有表头就按表头认列——`old_time` / `new_time` / `stage` 这几个可选列只有靠名字才认得出来；
    没表头就按位置认前三列。第四列起在老表里是 `rule` / `note` / `evidence`，
    按下标硬猜必然把它们当时间码。
    """
    raw = [ln for ln in decode(path).splitlines() if ln.strip()]
    if not raw:
        fail(f'改动表是空的：{path}')
    head = [x.strip().lower() for x in raw[0].split('\t')]
    named = {}
    for i, name in enumerate(head):
        key = CHANGE_COLS.get(name)
        if key and key not in named:
            named[key] = i
    if all(k in named for k in ('no', 'old', 'new')):
        cols, body = named, raw[1:]
    elif not head[0].split('\t')[0].strip().isdigit():
        fail(f'改动表第 1 行像是表头，但认不出必需列（要 no/old/new 或 条号/旧文本/新文本）：'
             f'{raw[0][:60]!r}')
    else:
        cols, body = {'no': 0, 'old': 1, 'new': 2}, raw

    rows = []
    for i, line in enumerate(body):
        parts = [x.strip() for x in line.split('\t')]

        def get(k):
            return parts[cols[k]] if k in cols and cols[k] < len(parts) else ''

        if not get('no').isdigit():
            fail(f'改动表第 {i + 1} 行首列不是条号：{line[:40]}')
        r = {'no': int(get('no')), 'old': get('old'), 'new': get('new'),
             'old_t': None, 'new_t': None, 'old_raw': None, 'new_raw': None,
             'stage': get('stage'), 'row': i + 1}
        ot, nt = get('old_time'), get('new_time')
        if bool(ot) != bool(nt):
            fail(f'改动表第 {i + 1} 行只声明了时间码的一侧（old_time={ot!r} / new_time={nt!r}）'
                 f'——两侧写全，机器不替你猜另一半变不变')
        if ot:
            a, b = split_pair(ot), split_pair(nt)
            if not a or not b:
                fail(f'改动表第 {i + 1} 行时间码认不出：{ot!r} / {nt!r}'
                     f'（写成 `开始-结束`，如 0:11:41.12-0:11:42.12）')
            r['old_t'], r['old_raw'] = a
            r['new_t'], r['new_raw'] = b
        rows.append(r)
    if not rows:
        fail(f'改动表是空的：{path}')
    seen, dup = set(), []
    for r in rows:
        if r['no'] in seen:
            dup.append(r['no'])
        seen.add(r['no'])
    if dup:
        print(f'改动表里同一条声明了多次：{sorted(set(dup))}'
              f'——一条字幕只能有一个最终文本；同一条的多处改动要先合成一行，'
              f'否则后一行会静默覆盖前一行', file=sys.stderr)
        sys.exit(2)
    return rows


def fail(msg):
    """参数/输入错一律退出码 2。`sys.exit("文字")` 的退出码是 **1**，和「复算不过」撞车，
    调用方就分不清是「声明写错了」还是「文件被改坏了」——这两件事的处置完全不同。
    """
    print(msg, file=sys.stderr)
    raise SystemExit(2)


def read_inserts(path):
    """读补行表 → [{start, end, cn, en, after_no, style, s, e}]。

    六列：start / end / cn / en / after_no / style。前两列是时间，cn 必填，
    en / after_no / style 留空表示「只补中文 / 不核对锚点 / 用锚点条样式」。
    """
    rows = []
    for i, line in enumerate(decode(path).splitlines()):
        if not line.strip():
            continue
        parts = [x.strip() for x in line.split('\t')]
        if parts[0].lower() == 'start':
            continue                                        # 表头
        if len(parts) < 3:
            fail(f'补行表第 {i + 1} 行不合格（至少三列：开始 / 结束 / 中文）：{line[:40]}')
        parts += [''] * (6 - len(parts))
        s, e = to_ms(parts[0]), to_ms(parts[1])
        if s is None or e is None:
            fail(f'补行表第 {i + 1} 行时间码认不出：{parts[0]!r} / {parts[1]!r}'
                     f'（ASS 写 0:19:06.52，SRT 写 00:19:06,520）')
        if not parts[2]:
            fail(f'补行表第 {i + 1} 行没有中文——补行不能是空的')
        if e <= s:
            fail(f'补行表第 {i + 1} 行结束不晚于开始：{parts[0]} → {parts[1]}')
        after = None
        if parts[4]:
            if not parts[4].isdigit():
                fail(f'补行表第 {i + 1} 行 after_no 不是条号：{parts[4]!r}')
            after = int(parts[4])
        rows.append({'start': parts[0], 'end': parts[1], 'cn': parts[2], 'en': parts[3],
                     'after_no': after, 'style': parts[5], 's': s, 'e': e, 'row': i + 1})
    if not rows:
        fail(f'补行表是空的：{path}')
    return rows


def locate(cues, ins):
    """按 start 找锚点条，返回 (前一条, 后一条)。位置不对抛 LookupError（带人话原因）。

    这是全流程唯一的「位置判定」，apply 和 verify 各跑一遍、结果必须相同——复算的意义
    就在这儿；**不许**改成读 after_no 直接插，那样人填错就只能等播放器跳行才发现。
    """
    s, e = ins['s'], ins['e']
    prev = next_ = None
    for c in cues:
        if c['s'] is None:
            continue
        if c['s'] <= s and (prev is None or c['s'] >= prev['s']):
            prev = c
        if c['s'] > s and (next_ is None or c['s'] < next_['s']):
            next_ = c
    if prev is None:
        raise LookupError(f'早于第一条字幕（{cues[0]["s"]}ms 起），不支持插在文件开头')
    if s < prev['e']:
        raise LookupError(f'落在第 {prev["no"]} 条的显示区间内，会和它同屏叠字')
    if next_ is not None and e > next_['s']:
        raise LookupError(f'结束时间超过下一条（第 {next_["no"]} 条）的开始，会叠字')
    if ins['after_no'] is not None and ins['after_no'] != prev['no']:
        raise LookupError(f'after_no 声明插在第 {ins["after_no"]} 条之后，'
                          f'按时间算应在第 {prev["no"]} 条之后——声明与时间对不上，先核时间轴')
    return prev, next_


def plan_inserts(cues, inserts):
    """把补行挂到锚点条上 → {锚点条号: [补行...]}，同一位置按开始时间升序。

    位置不对、或同一条补行被声明两次，一律退出码 2（不猜、不跳过的静默处理）。
    """
    by, seen = {}, set()
    for ins in inserts:
        key = (ins['s'], ins['cn'])
        if key in seen:
            fail(f'补行重复声明：{ins["start"]} {ins["cn"][:20]}'
                     f'——同一句补两遍会让条数对不上，也不会补出两条')
        seen.add(key)
        try:
            ins['_prev'], ins['_next'] = locate(cues, ins)
        except LookupError as ex:
            fail(f'补行第 {ins["row"]} 行（{ins["start"]} {ins["cn"][:16]}）位置不对：{ex}')
        by.setdefault(ins['_prev']['no'], []).append(ins)
    for v in by.values():
        v.sort(key=lambda x: x['s'])
    return by


def make_line(prev, ins):
    """照锚点行的前缀造一条新的 Dialogue 行（不含行尾）。

    Layer / Actor / 三个边距 / Effect 全抄锚点行——补行和它前后是同一种画面元素，
    样式跟着走才不会突然变大变小；style 给了就覆盖（歌词补行必须用歌词长样式）。
    """
    f = prev['prefix'].split(',')
    if len(f) < 9:
        fail(f'锚点行第 {prev["no"]} 条前缀认不出 Layer/Style，没法照抄：{prev["prefix"][:40]}')
    style = ins['style'] or f[3]
    text = ins['cn'] + (EN_MARK + ins['en'] if ins['en'] else '')
    return ','.join([f[0], ins['start'], ins['end'], style, f[4], f[5], f[6], f[7], f[8]]) + ',' + text


def new_zh(zh_raw, old, new):
    """替换后的中文段；锚点不对返回 None。"""
    if plain(zh_raw) == old:                    # 整条替换
        return new
    if zh_raw.count(old) != 1:                  # 0 次找不到；多次不唯一
        return None
    return zh_raw.replace(old, new, 1)


def apply(path, rows, inserts=None):
    """算新行列表。不落盘。返回 (新行列表, 实改条数, 失配列表, 实补行数)。

    先做替换、再按**原文件**的锚点插入——两边都基于同一份原文件，声明里的条号永远指
    改前那一版。反过来（先插入再替换）会让插入点之后的条号整体漂移，声明当场过期。
    """
    lines, cues, kind = load(path)
    bare = [ln.rstrip('\r\n') for ln in lines]
    eol = '\r\n' if lines and lines[0].endswith('\r\n') else '\n'
    by_no = {c['no']: c for c in cues}
    done, miss, added = 0, [], 0

    by = {}
    if inserts:
        if kind != 'ass':
            fail('补行只支持 ASS：SRT 每条占四行块、序号要重排，本版不做')
        by = plan_inserts(cues, inserts)

    for r in rows:
        no, old, new = r['no'], r['old'], r['new']
        c = by_no.get(no)
        if not c:
            miss.append((no, old, '条号不存在'))
            continue
        if r['new_t']:
            if kind != 'ass':
                miss.append((no, old, '时间码改动只支持 ASS，SRT 的要重排块结构'))
                continue
            if (c['s'], c['e']) != r['old_t']:
                miss.append((no, old, f'改前时间码 {c["s"]}–{c["e"]}ms 与声明 '
                                      f'{r["old_t"][0]}–{r["old_t"][1]}ms 对不上（双锚点）'))
                continue
        nz = new_zh(c['zh'], old, new)
        if nz is None:
            miss.append((no, old, '锚点失配（旧文本不在该条，或命中多次）'))
            continue

        if kind == 'ass':
            head = c['text'].split(EN_MARK)[0]      # 英文段之前的原始串（含标签、\N）
            rest = c['text'][len(head):]            # 英文段原样留住，一个字节不动
            prefix = c['prefix']
            if r['new_t']:
                f = prefix.split(',')               # Layer, Start, End, Style, …，末位空串
                f[1], f[2] = r['new_raw'][0], r['new_raw'][1]
                prefix = ','.join(f)
            bare[c['line']] = prefix + nz + rest
        else:
            zh_idx = [i for i in c['body'] if CJK.search(bare[i])]
            new_lines = nz.split('\n')
            if len(new_lines) != len(zh_idx):
                miss.append((no, old, f'中文占 {len(zh_idx)} 行、新文本 {len(new_lines)} 行——折行不归这条工具管'))
                continue
            for i, v in zip(zh_idx, new_lines):
                bare[i] = v
        done += 1

    out = []
    cue_at = {c['line']: c for c in cues}
    for i, ln in enumerate(bare):
        out.append(ln)
        c = cue_at.get(i)
        if c and c['no'] in by:
            for ins in by[c['no']]:                 # 已按开始时间升序
                out.append(make_line(c, ins))
                added += 1

    return [ln + eol for ln in out], done, miss, added


def encoding_of(path):
    """原文件用的编码。写回时必须用它——统一写成 utf-8-sig 会凭空**多加一个 BOM**，
    行内容一个字没变，但文件的头两个字节变了，某些播放器/工具会认成另一个文件。

    这条是自测时抓到的：第一版一律写 utf-8-sig，diff 第一行从 `1` 变成 `\\ufeff1`，
    而复算按行比看不出来（BOM 已在解码时被吃掉）。现在写之前先看，复算也查 BOM 状态。
    """
    raw = path.read_bytes()
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff') or b'\x00' in raw[:8]:
        return 'utf-16'
    return 'utf-8-sig' if raw[:3] == b'\xef\xbb\xbf' else 'utf-8'


def has_bom(path):
    return path.read_bytes()[:3] == b'\xef\xbb\xbf'


def cue_text(kind, bare, c):
    """一条在复核时的「可比较原文」：ASS 取文本字段，SRT 取正文各行。"""
    if kind == 'ass':
        return bare[c['line']]
    return '\n'.join(bare[i] for i in c['body'])


def verify(before_path, after_path, rows, inserts=None, allow_lines=0):
    """五条判据：行数、条数、声明条目到位、补行内容与时间、未声明条目一字未变。

    allow_lines：除补行外还允许的行数变化，默认 0。只有 `accept` 会传——
    样式表增删样式本来就要改行数，那是文件层的活，不是本工具管的「文本替换」。
    传进来的值必须由调用方算得出出处，不能是「差不多就行」。

    对位不按下标硬套：先按改前文件重算一遍「应有的条号序列」（原条与补行交错），
    再拿改后文件逐位比。**插一行就让后面全体条号漂移**，旧版 `bc[no-1]` 那种对法在
    有补行时会把整条序列错位比对，全表报红或全表漏报——这层对齐是补行能不能复算的关键。
    """
    bl, bc, kind = load(before_path)
    al, ac, _ = load(after_path)
    bb = [ln.rstrip('\r\n') for ln in bl]
    ab = [ln.rstrip('\r\n') for ln in al]
    inserts = inserts or []
    issues = []

    by = {}
    if inserts:
        if kind != 'ass':
            issues.append('补行只支持 ASS，SRT 文件不该带 --inserts')
        else:
            by = plan_inserts(bc, inserts)

    expected = []
    for c in bc:
        expected.append(('keep', c['no']))
        for ins in by.get(c['no'], []):
            expected.append(('ins', ins))

    want_lines = len(bb) + len(inserts) + allow_lines
    if len(ab) != want_lines:
        issues.append(f'行数应是 {len(bb)} + {len(inserts)} 补行'
                      + (f' + {allow_lines} 其它' if allow_lines else '')
                      + f' = {want_lines}，实际 {len(ab)}（多一行少一行都是改错了地方）')
    if len(ac) != len(expected):
        issues.append(f'条数应是 {len(bc)} + {len(inserts)} = {len(expected)}，实际 {len(ac)}')
    if has_bom(Path(before_path)) != has_bom(Path(after_path)):
        issues.append('BOM 状态变了（文件头两个字节被改）——内容没变但文件变了')
    if len(ab) != want_lines or len(ac) != len(expected):
        return issues, {'声明': len(rows), '补行': 0, '在位': 0, '不符': len(rows), '多改': 0}

    want = {r['no']: r for r in rows}
    ok = bad = ins_ok = 0
    dirty = []
    for pos, (ki, key) in enumerate(expected):
        if ki == 'ins':
            want_text = key['cn'] + (EN_MARK + key['en'] if key['en'] else '')
            if ac[pos]['text'] != want_text:
                issues.append(f'补行（{key["start"]}）文本不对：实际「{ac[pos]["text"][:24]}」')
                bad += 1
            elif (ac[pos]['s'], ac[pos]['e']) != (key['s'], key['e']):
                issues.append(f'补行（{key["start"]}）时间不对：实际 {ac[pos]["s"]}–{ac[pos]["e"]} 毫秒')
                bad += 1
            else:
                ok += 1
                ins_ok += 1
            continue

        cb, ca = bc[key - 1], ac[pos]
        if key in want:
            r = want[key]
            old, new = r['old'], r['new']
            # 期望值从「改前 + 声明」重算一遍，不看 apply 怎么说——复算的意思就是不信任上一次
            expect = new_zh(cb['zh'], old, new)
            if expect is None:
                issues.append(f'条 {key}：旧文本「{old[:20]}」在改前就找不到，声明与文件对不上')
                bad += 1
            elif ca['zh'].strip() != expect.strip():
                issues.append(f'条 {key}：改后是「{ca["zh"][:24]}」，按声明应是「{expect[:24]}」')
                bad += 1
            elif r['new_t'] and (cb['s'], cb['e']) != r['old_t']:
                issues.append(f'条 {key}：改前时间码 {cb["s"]}–{cb["e"]} 毫秒与声明不符——'
                              f'时间码也走双锚点，基线换了就得重写声明')
                bad += 1
            elif r['new_t'] and (ca['s'], ca['e']) != r['new_t']:
                issues.append(f'条 {key}：改后时间码 {ca["s"]}–{ca["e"]} 毫秒，'
                              f'按声明应是 {r["new_t"][0]}–{r["new_t"][1]}')
                bad += 1
            else:
                ok += 1
        elif cue_text(kind, bb, cb) != cue_text(kind, ab, ca):
            dirty.append(key)

    if dirty:
        issues.append(f'未声明的 {len(dirty)} 条也被改了：{dirty[:8]}')

    return issues, {'声明': len(want), '补行': ins_ok, '在位': ok,
                    '不符': bad, '多改': len(dirty)}


def stat_line(st):
    return (f'复算：声明 {st["声明"]} / 补行 {st["补行"]} / 在位 {st["在位"]}'
            f' / 不符 {st["不符"]} / 多改 {st["多改"]}')


def main():
    ap = argparse.ArgumentParser(description='落地：把改动写进成品，再证明只改了该改的')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p1 = sub.add_parser('apply')
    p1.add_argument('subtitle')
    p1.add_argument('changes', nargs='?', help='改动表；只补行不给字时可以留空')
    p1.add_argument('--inserts', help='补行表（只支持 ASS）')
    p1.add_argument('-o', '--out')
    p1.add_argument('--in-place', action='store_true')
    p1.add_argument('--dry-run', action='store_true')
    p2 = sub.add_parser('verify')
    p2.add_argument('before')
    p2.add_argument('after')
    p2.add_argument('changes', nargs='?')
    p2.add_argument('--inserts', help='补行表（只支持 ASS）')
    a = ap.parse_args()

    def load_all():
        """两张表都读出来。两张都空就没活可干——报错，别默默成功。"""
        rows = read_changes(Path(a.changes)) if a.changes else []
        ins = read_inserts(Path(a.inserts)) if a.inserts else []
        if not rows and not ins:
            fail('既没有改动表也没有补行表，没有可落地的内容')
        return rows, ins

    if a.cmd == 'verify':
        rows, ins = load_all()
        issues, st = verify(Path(a.before), Path(a.after), rows, ins)
        print(stat_line(st))
        for x in issues:
            print('  ERROR', x)
        return 1 if issues else 0

    src = Path(a.subtitle)
    rows, ins = load_all()
    out, done, miss, added = apply(src, rows, ins)
    for no, old, why in miss:
        print(f'  跳过 条{no}：{why}（{old[:24]}）')
    print(f'落地：应改 {len(rows)} 条，实改 {done} 条，跳过 {len(miss)} 条；'
          f'应补 {len(ins)} 条，实补 {added} 条')
    # 有跳过就不许落盘：半改比不改更糟，声明与文件已经对不上了
    if miss:
        print('  有锚点失配，未写盘。改完声明再跑一次。')
        return 2
    if a.dry_run:
        return 0 if (done or added) else 3

    before = src
    if a.in_place:
        dst = src
        bak = src.with_suffix(src.suffix + '.bak')
        if not bak.exists():
            shutil.copy2(src, bak)
            print(f'  备份：{bak.name}')
        # 写盘后 src 就被覆盖了：先留一份「改前」副本，否则 verify 拿改后比改后，
        # 必然报满「旧文本在改前就找不到」——文件明明改对了，结论却是打回。
        before = src.with_name('.' + src.stem + '.before' + src.suffix)
        shutil.copy2(src, before)
    else:
        dst = Path(a.out) if a.out else src.with_suffix('.fixed' + src.suffix)

    try:
        dst.write_bytes(''.join(out).encode(encoding_of(src)))
        issues, st = verify(before, dst, rows, ins)
    finally:
        if before is not src and before.exists():
            before.unlink()
    print(stat_line(st))
    for x in issues:
        print('  ERROR', x)
    if issues:
        return 1
    print(f'  输出：{dst}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
