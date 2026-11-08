#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-05（新建：最小回归集；同日加「同条重复声明被拒」正反两条）
"""selftest.py -- 五件工具的最小回归

为什么有它：2026-11-05 写分诊器时，同一条规则连改三次，全靠肉眼看输出对不对。
肉眼看不出「这次改坏了上一版能过的用例」——那正是回归集要干的活。
本文件只跑得起眼的断言：时间码解析、配对、分诊分档，各几条。

用法：
    python tools/selftest.py            # 全跑，退出码 1 = 有用例失败
    python tools/selftest.py -v         # 每条都打印

规矩：断言必须定位到目标对象（取那一条再断言），不许拿整个结果表做 in 判断——
表里别的项会顶包，假通过比失败更贵。
"""
import argparse
import contextlib
import datetime
import io
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import align_bilingual as al          # noqa: E402
import subtitle_probe as sp           # noqa: E402
import triage_diff as td              # noqa: E402
import bilingual_pairs as bp          # noqa: E402
import land_verify as lv              # noqa: E402
import style_normalize as sn          # noqa: E402
import term_candidates as tc          # noqa: E402
import accept as ac                  # noqa: E402
import build_base as bb              # noqa: E402
import text_scan as ts               # noqa: E402
import check_rules as cr             # noqa: E402

PASS, FAIL = [], []
VERBOSE = False


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    if VERBOSE or not cond:
        print(f'{"ok  " if cond else "FAIL"}  {name}' + (f'  —— {detail}' if detail and not cond else ''))


def tmp_srt(text, name='t.srt'):
    p = Path(tempfile.mkdtemp()) / name
    p.write_bytes(text.encode('utf-8'))
    return p


def t_probe():
    """体检：条数、坏时长、金额问号（只在文本区查）。"""
    srt = ('1\n00:00:01,000 --> 00:00:02,000\n票价?8\n\n'
           '2\n00:00:03,000 --> 00:00:02,500\n时间倒过来\n\n')
    r = sp.probe(tmp_srt(srt))
    check('体检/条数=2', r['cues'] == 2, f'实际 {r["cues"]}')
    check('体检/坏时长=1', r['dur_bad'] == 1, f'实际 {r["dur_bad"]}')
    check('体检/金额问号=1', r['money_q'] == 1, f'实际 {r["money_q"]}')

    srt2 = '1\n00:00:01,000 --> 00:00:02,000\nat 8? 正常问句\n\n'
    r2 = sp.probe(tmp_srt(srt2, 'q.srt'))
    check('体检/正常问句不算金额问号', r2['money_q'] == 0, f'实际 {r2["money_q"]}')


def t_align():
    """对齐：中文当骨架，一条挂 0 或 1 条英文，挂不上要留行。

    时间轴要够长：片头 90 秒内、片尾 30 秒内的短句都会被判成标题卡，
    那是另一条规则的事，拿几秒的假数据测会把两条规则混在一起。
    """
    zh = [{'no': 1, 's': 300000, 'e': 301000, 'text': '第一句'},
          {'no': 2, 's': 301000, 'e': 302000, 'text': '第二句'},
          {'no': 3, 's': 302000, 'e': 303000, 'text': '第三句'},
          {'no': 4, 's': 400000, 'e': 401000, 'text': '第四句'}]
    en = [{'no': 1, 's': 300000, 'e': 301000, 'text': 'First.'},
          {'no': 2, 's': 302000, 'e': 303000, 'text': 'Third.'},
          {'no': 3, 's': 400000, 'e': 401000, 'text': 'Fourth.'}]
    rows, used, pairs = al.run(zh, en)
    check('对齐/行数不丢=4', len(rows) == 4, f'实际 {len(rows)}')
    check('对齐/配上 3 条', len(pairs) == 3, f'实际 {len(pairs)}')
    mid = rows[1]
    check('对齐/中间那条未挂上', mid['en'] is None, '中间那条不该配上')
    check('对齐/未挂上落「无候选」', mid['flag'] == '未配对:无候选', f'实际 {mid["flag"]}')
    check('对齐/第三条配到英文第 2 条', pairs.get(2) == 1, f'实际 {pairs.get(2)}')
    check('对齐/置信度有值', rows[0]['conf'] in ('1.00', '0.95', '0.90'), rows[0]['conf'])

    # 反向：片头窗口内的短句应当被判成标题卡，不是「无候选」
    zh2 = [{'no': 1, 's': 5000, 'e': 6000, 'text': '第1季第1集'}]
    rows2, _, _ = al.run(zh2, [{'no': 1, 's': 60000, 'e': 61000, 'text': 'Later.'}])
    check('对齐/片头短句判成标识', rows2[0]['flag'] == '未配对:标识', f'实际 {rows2[0]["flag"]}')


def t_triage():
    """分诊：T1 漏译 / T2 误译 / T3 不挑，各验正反。"""
    cases = [
        # (说明, 中文, 参照, 期望代号)
        ('漏句落 T1', '祝福首相', 'Blessed be the name of the Prime Minister', 'T1'),
        ('数字错落 T2', '第100卷', 'Volume 200, page 3.', 'T2'),
        ('整条英文落 T1', 'We are the champions my friend', 'We are the champions my friend', 'T1'),
        ('月份写法差异不报', '我在去年5月2号去过', 'I went there on May the second last year.', None),
        ('单个专名不报', '这是BBC', 'This is the BBC.', None),
        ('正常句不报', '今天天气不错', 'Nice weather today.', None),
        ('短句不报', '来了!', 'Here we are!', None),
        ('标题卡不报', '某剧 第1季第1集', '', None),
    ]
    for i, (why, zh, ref, want) in enumerate(cases):
        mark = '未配对:标识' if i == 7 else ''
        code = td.judge(zh, ref, [], mark)[0]
        check(f'分诊/{why}', code == want, f'期望 {want}，实际 {code}')

    check('分诊/中文数字换算', td.cn_value('2万1千793') == '21793', td.cn_value('2万1千793'))
    check('分诊/千分位换算', td.cn_value('21,793') == '21793', td.cn_value('21,793'))

    # 数字归一化：9 集实测一次误报 15 条，全是这几类写法。每类正反各一条。
    num = [
        ('中文「万」不带后随数字', td.cn_value('20万'), '200000'),
        ('中文「X万Y千」', td.cn_value('2万3千'), '23000'),
        ('中文「X千Y百万」按 3200 个万算', td.cn_value('3千2百万'), '32000000'),
        ('中文「X千Y百万」第二位', td.cn_value('2千5百万'), '25000000'),
        ('中文时间「7点45」并成 745', '745' in td.nums_of('坐7点45的火车'), str(td.nums_of('坐7点45的火车'))),
        ('参照点号千分位', '23000' in td.ref_readings('23.000'), str(td.ref_readings('23.000'))),
        ('参照时间点号按整数', '10' in td.ref_readings('10.00'), str(td.ref_readings('10.00'))),
        ('参照时间冒号', '745' in td.ref_readings('7:45'), str(td.ref_readings('7:45'))),
        ('参照真小数乘量级', '7500000' in td.ref_readings('7.5', 'million'), str(td.ref_readings('7.5', 'million'))),
        ('参照量级词', '32000000' in td.ref_readings('32', 'million'), str(td.ref_readings('32', 'million'))),
        ('参照逗号千分位', '200000' in td.ref_readings('200,000'), str(td.ref_readings('200,000'))),
    ]
    for why, got, want in num:
        check(f'数字/{why}', got, want)

    # 双向：修完之后「真错的仍然抓到」+「假红的必须放过」
    both = [
        ('真错仍抓（卷号 487 对 497）', '英国国会议事录 第487卷 第1102页', 'Hansard Volume 497, page 1102, Column B.', 'T2'),
        ('真错仍抓（7点45 对 7.55）', '坐7点45的火车', 'catch the 7.55 train', 'T2'),
        ('假红放过（2万3千 对 23.000）', '我想 大概2万3千人吧', 'about 23.000 I think, Minister.', None),
        ('假红放过（3千2百万 对 32 million）', '但节省3千2百万开支的事', 'saving 32 million pounds', None),
        ('假红放过（20万 对 200,000）', '裁掉20万名公务员', 'about 200,000 people.', None),
        ('假红放过（7点45 对 7.45）', '你要坐7点45的火车', "You'll catch the 7.45 train", None),
        ('假红放过（9.75% 对 9.75）', '因为地方政府部门扩张了9.75%', 'There was a 9.75 rise', None),
        ('假红放过（比例 1:10 对 1:10）', '师生比例会从1:10变成1:34',
         'The staff/student ratio would go from 1:10 to 1:34', None),
        ('假红放过（缩写撑大词数）', '这根本不是个T.P.L.A.C.',
         'It\'s not a T.P. L.A.C. At all.', None),
        ('假红放过（倒计时纯数字行）', '5 4 3 2 …', 'Five. Four. Three. Two...', None),
        ('假红放过（勋章缩写）', '先拿CMG 然后拿KCMG 最后拿GCMG',
         'First you get the CMG, then the KCMG and the GCMG,', None),
        ('不放过（整条英文仍判未译）', 'We are the champions', 'We are the champions my friend', 'T1'),
    ]
    for why, zh, ref, want in both:
        code = td.judge(zh, ref, [], '')[0]
        check(f'分诊/{why}', code == want, f'期望 {want}，实际 {code}')

    # 错位区间：人划的表，区间内跳过不判。三件都要验：读得进、跳得掉、别越界。
    d = Path(tempfile.mkdtemp())
    off, bad = d / 'off.tsv', d / 'bad.tsv'
    off.write_text('起条号\t止条号\t理由\n10\t12\t西语段落，中英整体错开一句\n',
                   encoding='utf-8', newline='')
    spans = td.read_offsets(off)
    check('错位/读表', spans == [(10, 12, '西语段落，中英整体错开一句')], str(spans))
    check('错位/区间内命中', td.in_span('11', spans) == '西语段落，中英整体错开一句', td.in_span('11', spans))
    # 边界必须算在内——漏判一条就是让审稿照旧逐条比
    check('错位/左边界算在内', td.in_span('10', spans) is not None, '起条号没算进去')
    check('错位/右边界算在内', td.in_span('12', spans) is not None, '止条号没算进去')
    # 反方向：区间外一条都不许吞，否则整段真错会被一起盖掉
    check('错位/区间外不吞', td.in_span('13', spans) is None and td.in_span('9', spans) is None,
          f'9={td.in_span("9", spans)} 13={td.in_span("13", spans)}')
    check('错位/非数字条号不误判', td.in_span('', spans) is None, '空条号被判进区间')

    # 反向：止条号小于起条号要拒（写反了会把整段区间吞成空）
    bad.write_text('20\t15\t写反了\n', encoding='utf-8', newline='')
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            td.read_offsets(bad)
        check('错位/区间写反要拒', False, '没拒，20–15 这种区间会被静默当成空')
    except SystemExit as e:
        check('错位/区间写反要拒', e.code == 2, f'退出码 {e.code}')

    # stage 列：几条清单并成一张时靠它分清是谁判的，少了这一列就分不清
    check('分诊/stage 列已在表头', 'stage' in td.HEAD, str(td.HEAD))


def t_pairs():
    """双语字幕直切：ASS 的 \\N{\\rEnglish}、SRT 的双行，都要切得开、卡要认得出。"""
    ass = ('[Events]\n'
           'Format: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n'
           'Dialogue: 0,0:00:40.30,0:00:41.03,Default,NTP,0,0,0,,就是今晚\\N{\\rEnglish}Tonight\'s the night.\n'
           'Dialogue: 0,0:01:40.00,0:01:41.00,Default,NTP,0,0,0,,压制：beimi\\N{\\rEnglish}THE LAST FANTASY\n'
           'Dialogue: 0,0:05:00.00,0:05:01.00,Default,NTP,0,0,0,,只有中文没有英文的一行\n'
           'Dialogue: 0,0:05:02.00,0:05:03.00,Mark,NTP,0,0,0,,字幕组署名\n')
    p = Path(tempfile.mkdtemp()) / 'a.ass'
    p.write_bytes(ass.encode('utf-8'))
    rows = bp.pairs_of(p)
    check('双语/ASS 条数=4', len(rows) == 4, f'实际 {len(rows)}')
    check('双语/ASS 切出中文', rows[0]['zh'] == '就是今晚', rows[0]['zh'])
    check('双语/ASS 切出英文', rows[0]['en'] == "Tonight's the night.", rows[0]['en'])
    check('双语/组标标成标识', bp.flag_of(rows[1], 90000, 600000) == '未配对:标识',
          bp.flag_of(rows[1], 90000, 600000))
    check('双语/无英文不丢行', bp.flag_of(rows[2], 90000, 600000) != '', '空的标记列')
    check('双语/Mark 样式标成标识', bp.flag_of(rows[3], 90000, 600000) == '未配对:标识',
          bp.flag_of(rows[3], 90000, 600000))

    srt = ('1\n00:00:40,300 --> 00:00:41,030\n就是今晚\nTonight\'s the night.\n\n'
           '2\n00:05:00,000 --> 00:05:01,000\n只有中文\n\n')
    q = Path(tempfile.mkdtemp()) / 'b.srt'
    q.write_bytes(srt.encode('utf-8'))
    r2 = bp.pairs_of(q)
    check('双语/SRT 条数=2', len(r2) == 2, f'实际 {len(r2)}')
    check('双语/SRT 双行切开', (r2[0]['zh'], r2[0]['en']) == ('就是今晚', "Tonight's the night."),
          str((r2[0]['zh'], r2[0]['en'])))
    check('双语/SRT 单行留行', r2[1]['en'] == '' and r2[1]['zh'] == '只有中文',
          str((r2[1]['zh'], r2[1]['en'])))


def t_land():
    """落地：只改中文段、不动英文/时间码/行数，且能抓出「多改」。"""
    ass = ('[Events]\nFormat: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n'
           'Dialogue: 0,0:00:40.30,0:00:41.03,Default,NTP,0,0,0,,就是今晚\\N{\\rEnglish}Tonight\'s the night.\n'
           'Dialogue: 0,0:00:44.00,0:00:45.00,Default,NTP,0,0,0,,别动这一条\\N{\\rEnglish}Leave this one.\n')
    d = Path(tempfile.mkdtemp())
    src, ch = d / 'a.ass', d / 'c.tsv'
    src.write_bytes(ass.encode('utf-8'))                     # 故意不带 BOM
    ch.write_text('条号\t旧文本\t新文本\n1\t就是今晚\t正是今晚\n', encoding='utf-8', newline='')

    out, done, miss, added = lv.apply(src, lv.read_changes(ch))
    check('落地/实改条数=1', done == 1 and not miss, f'done={done} miss={miss}')
    check('落地/没有补行', added == 0, f'added={added}')
    check('落地/行数不变', len(out) == len(ass.splitlines()), f'{len(out)}')
    check('落地/英文段原样', "\\N{\\rEnglish}Tonight's the night." in out[2], out[2][:60])
    check('落地/中文段已换', '正是今晚' in out[2], out[2][:60])
    check('落地/别的条没动', out[3].rstrip('\r\n') == ass.splitlines()[3], out[3][:60])

    dst = d / 'b.ass'
    dst.write_bytes(''.join(out).encode(lv.encoding_of(src)))
    issues, st = lv.verify(src, dst, lv.read_changes(ch))
    check('落地/复算通过', not issues and st['多改'] == 0, str(issues))
    check('落地/不凭空加 BOM', not lv.has_bom(dst), '写回时加了 BOM')

    # 反向①：锚点对不上就不改（宁可不改，不许猜）
    ch2 = d / 'c2.tsv'
    ch2.write_text('1\t根本没有这句\tx\n', encoding='utf-8', newline='')
    _o, done2, miss2, _a = lv.apply(src, lv.read_changes(ch2))
    check('落地/锚点失配不改', done2 == 0 and len(miss2) == 1, f'done={done2} miss={len(miss2)}')

    # 反向②：偷改一条没声明的，复算要抓出来
    bad = d / 'c.ass'
    bad.write_bytes(''.join(out).replace('别动这一条', '偷改这一条').encode(lv.encoding_of(src)))
    issues2, st2 = lv.verify(src, bad, lv.read_changes(ch))
    check('落地/复算抓多改', st2['多改'] == 1 and any('未声明' in x for x in issues2), str(issues2))

    # 反向③：同一条声明两次必须拒。
    # 2026-11-05 实测过不拒的后果：apply 报「应改 2 实改 2」、verify 报「声明 1 / 在位 1 /
    # 多改 0」退出码 0，前一条被后一条静默覆盖——复算全绿，改动却少了。
    ch3 = d / 'c3.tsv'
    ch3.write_text('1\t就是今晚\t正是今晚\n1\t就是今晚\t今晚就是\n', encoding='utf-8', newline='')
    try:
        with contextlib.redirect_stderr(io.StringIO()):       # 拒的时候会往 stderr 打一行
            lv.read_changes(ch3)
        check('落地/同条重复声明被拒', False, '没拒，重复条号会静默覆盖前一条')
    except SystemExit as e:
        check('落地/同条重复声明被拒', e.code == 2, f'退出码应是 2，实际 {e.code}')
    # 反向④：重复的必须是「同一条」，两条不同条号不许被误拦
    ch4 = d / 'c4.tsv'
    ch4.write_text('1\t就是今晚\t正是今晚\n2\t别动这一条\t别动这条\n', encoding='utf-8', newline='')
    check('落地/不同条号不误拦', len(lv.read_changes(ch4)) == 2, '不同条号被误拦了')


def t_accept():
    """验收：数字报全；「条数行数都对、只有补行位置错」必须抓得到——那正是跳行的形态。"""
    head = ('[Events]\nFormat: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n'
            'Dialogue: 0,0:00:10.00,0:00:12.00,Default,NTP,0,0,0,,第一句\\N{\\rEnglish}First.\n'
            'Dialogue: 0,0:00:20.00,0:00:22.00,Default,NTP,0,0,0,,第二句\\N{\\rEnglish}Second.\n'
            'Dialogue: 0,0:00:30.00,0:00:32.00,Default,NTP,0,0,0,,第三句\\N{\\rEnglish}Third.\n')
    d = Path(tempfile.mkdtemp())
    src = d / 'a.ass'
    src.write_bytes(head.encode('utf-8'))
    ins_tsv = d / 'ins.tsv'
    ins_tsv.write_text('start\tend\tcn\ten\tafter_no\tstyle\n'
                       '0:00:25.00\t0:00:27.00\t漏掉的一句\t\t2\t\n', encoding='utf-8', newline='')
    ins = lv.read_inserts(ins_tsv)
    out, _dn, _ms, _ad = lv.apply(src, [], ins)
    dst = d / 'b.ass'
    dst.write_bytes(''.join(out).encode('utf-8'))

    marks, code = ac.check(src, dst, [], ins)
    check('验收/正常通过', code == 0, str([m for l, m in marks if l == '✗']))
    check('验收/条数与对白行都报',
          any('条数' in m for _, m in marks) and any('对白行' in m for _, m in marks), '缺项')
    check('验收/没给档位只是提醒不算不过', any(l == '⚠' and '档位' in m for l, m in marks), str(marks[-1]))

    # 反向：把补行挪到文件末尾 → 条数、行数照样对，只有位置错
    ls = dst.read_bytes().split(b'\n')
    k = next(x for x, ln in enumerate(ls) if '漏掉的一句'.encode() in ln)
    moved = ls.pop(k)
    last = max(x for x, ln in enumerate(ls) if ln.startswith(b'Dialogue:'))
    ls.insert(last + 1, moved)
    bad = d / 'c.ass'
    bad.write_bytes(b'\n'.join(ls))

    marks2, code2 = ac.check(src, bad, [], ins)
    check('验收/挪位要抓', code2 == 1 and any('补行位置' in m for l, m in marks2 if l == '✗'),
          str([m for l, m in marks2 if l == '✗'][:3]))
    check('验收/挪位给连带提示', any(l == '!' for l, _ in marks2), '没提示那些「未声明被改」是连带')

    # 反向：漏补行（after 就是 before）
    marks3, code3 = ac.check(src, src, [], ins)
    check('验收/少补要抓', code3 == 1 and any('条数' in m for l, m in marks3 if l == '✗'),
          str([m for l, m in marks3 if l == '✗'][:2]))

    # 反向：样式表增删样式会改行数，但那是文件层的活，不该报 ✗
    # （端到端演练时抓出来的：样式器给样式表加一条 Mark，验收把行数判成错）
    def mk(n_style, extra=''):
        s = ('[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize\n')
        s += ''.join('Style: S%d,Arial,20\n' % i for i in range(n_style))
        s += ('\n[Events]\nFormat: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n')
        s += 'Dialogue: 0,0:00:10.00,0:00:12.00,Default,NTP,0,0,0,,' + '\u7b2c\u4e00\u53e5' + '\n'
        return s + extra

    f1, f2 = d / 's1.ass', d / 's2.ass'
    f1.write_bytes(mk(1).encode('utf-8'))
    f2.write_bytes(mk(2).encode('utf-8'))
    marks4, code4 = ac.check(f1, f2, [], [])
    check('验收/样式表增行不报错', code4 == 0, str([m for l, m in marks4 if l == '✗']))
    check('验收/非对白行变化要说出来', any('非对白行' in m for _, m in marks4),
          str([m for _, m in marks4]))

    # 反向：多一条对白必须抓——那才是真错
    f3 = d / 's3.ass'
    f3.write_bytes(mk(1, 'Dialogue: 0,0:00:20.00,0:00:22.00,Default,NTP,0,0,0,,'
                        + '\u591a\u7684' + '\n').encode('utf-8'))
    marks5, code5 = ac.check(f1, f3, [], [])
    check('验收/多一条对白要抓', code5 == 1, str([m for l, m in marks5 if l == '✗']))


def t_base():
    """底本合成：两份单语 SRT → 双语 ASS。双语结构、标签、样式补全、编码都要对。"""
    d = Path(tempfile.mkdtemp())
    zh = d / 'z.srt'
    en = d / 'e.srt'
    zh.write_text('1\n00:00:01,000 --> 00:00:03,000\n第一句\n\n'
                  '2\n00:00:04,000 --> 00:00:06,000\n'
                  '<font color="#4096d1">\n片头卡片\n</font>\n\n'
                  '3\n00:00:07,000 --> 00:00:09,000\n只有中文\n', encoding='utf-8')
    en.write_text('1\n00:00:01,200 --> 00:00:03,200\nFirst line.\n', encoding='utf-8')

    cues_z = lv.parse_srt(zh)[0] if hasattr(lv, 'parse_srt') else None
    z, _b1 = bb.parse_srt(zh)
    e, _b2 = bb.parse_srt(en)
    text, st = bb.build(z, e, 'T')
    lines = [ln for ln in text.split('\n') if ln.startswith('Dialogue:')]

    check('底本/条数=中文条数', len(lines) == 3, f'{len(lines)}')
    check('底本/配对条有英文段', '\\N{\\rEnglish}First line.' in lines[0], lines[0][:60])
    check('底本/未配对只写中文', '{\\rEnglish}' not in lines[2], lines[2][:60])
    check('底本/统计对得上', st['配对'] == 1 and st['未配对'] == 2, str(st['配对']) + '/' + str(st['未配对']))

    # 标签：颜色要转 BGR（#4096d1 → &Hd19640&），且标签常独占一行、不能留下空视觉行
    card = [ln for ln in lines if '片头卡片' in ln][0]
    check('底本/颜色转 BGR', '{\\c&Hd19640&}' in card, card[-50:])
    check('底本/标签独占行被归并', '\\N片头卡片' not in card and card.count('\\N') == 0, card[-40:])
    check('底本/不留裸 HTML', '<font' not in text and '<i>' not in text, '有残留')

    # 样式：默认样式表必须自带 English，否则 {\\rEnglish} 没有落点
    check('底本/默认样式含 English', 'Style: English,' in text, '缺 English 样式')

    # --style-from：PlayRes 是 0 要按规范补 384×288；缺 English 要从 Default 复制
    ref = d / 'ref.ass'
    ref.write_bytes(('[Script Info]\r\nPlayResX: 0\r\nPlayResY: 0\r\n\r\n'
                     '[V4+ Styles]\r\nFormat: Name, Fontname, Fontsize, Outline, Shadow, '
                     'Alignment, MarginL, MarginR, MarginV, Encoding\r\n'
                     'Style: Default,FZ,20,2,1,2,5,5,2,134\r\n\r\n[Events]\r\n').encode('utf-8'))

    # 不给 --video：沿用源画布、不换算，并且必须说出「可能会拉伸」
    text2, st2 = bb.build(z, e, 'T', str(ref))
    check('底本/PlayRes 0 按 384×288 理解', 'PlayResX: 384' in text2 and 'PlayResY: 288' in text2,
          [ln for ln in text2.split('\n') if 'PlayRes' in ln])
    check('底本/不给 video 要提示拉伸风险', any('拉伸' in n for n in st2['提示']), str(st2['提示']))
    check('底本/抄样式后补 English', 'Style: English,FZ,15,' in text2,
          [ln for ln in text2.split('\n') if ln.startswith('Style:')])

    # 给 --video：按轴换算。字号/描边/纵边距走 y（×3.75），横边距走 x（×5）
    text3, st3 = bb.build(z, e, 'T', str(ref), (1920, 1080))
    dflt = [ln for ln in text3.split('\n') if ln.startswith('Style: Default')][0]
    parts = dflt.split(',')
    check('底本/换算目标画布', 'PlayResX: 1920' in text3 and 'PlayResY: 1080' in text3,
          [ln for ln in text3.split('\n') if 'PlayRes' in ln])
    check('底本/字号走 y 轴', parts[2] == '75', f'字号 {parts[2]}（20×3.75 应 75）')
    check('底本/横边距走 x 轴', parts[6] == '25' and parts[7] == '25',
          f'MarginL/R {parts[6]}/{parts[7]}（5×5 应 25）')
    check('底本/纵边距走 y 轴', parts[8] == '8', f'MarginV {parts[8]}（2×3.75 应 8）')
    check('底本/换算要报算式', any('换到' in n for n in st3['提示']), str(st3['提示']))

    # 落盘格式：BOM + CRLF，下游（land_verify）要能读
    out = d / 'o.ass'
    out.write_bytes(b'\xef\xbb\xbf' + text.replace('\n', '\r\n').encode('utf-8'))
    raw = out.read_bytes()
    check('底本/写 BOM 与 CRLF', raw[:3] == b'\xef\xbb\xbf' and raw.count(b'\r\n') == text.count('\n'),
          f'BOM={raw[:3] == b"\xef\xbb\xbf"}')
    _l, cues, kind = lv.load(out)
    check('底本/落地器读得回', kind == 'ass' and len(cues) == 3 and cues[0]['en'].strip() == 'First line.',
          f'{len(cues)} 条')

    # 参数错：缺文件要退出码 2，不能静默产出空底本
    import subprocess
    r = subprocess.run([sys.executable, str(TOOLS / 'build_base.py'), str(zh), str(d / 'nope.srt'),
                        '-o', str(d / 'x.ass')], capture_output=True, text=True)
    check('底本/缺文件退出码 2', r.returncode == 2, f'退出码 {r.returncode}')


def t_base_make():
    """基准换算：从现成基准按视频分辨率造新基准。16:9 基准配 4:3 的剧就靠它。"""
    d = Path(tempfile.mkdtemp())
    src = d / 'b.ass'
    src.write_bytes(('[Script Info]\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\n'
                     'Format: Name, Fontname, Fontsize, Outline, Shadow, Alignment, '
                     'MarginL, MarginR, MarginV, Encoding\n'
                     'Style: Default,FZ,80,8,4,2,60,60,20,1\n'
                     'Style: English,FZ,54,8,4,2,60,60,20,1\n\n'
                     '[Events]\nFormat: Layer, Start, End\n').encode('utf-8'))

    def style_line(txt, name='Default'):
        return [ln for ln in txt.splitlines() if ln.startswith('Style: ' + name)][0].split(',')

    # 同高换宽（16:9 → 4:3）：字号不动，横边距按 x 缩
    o1 = d / 'o1.ass'
    sn.cmd_base(src, (1440, 1080), o1)
    txt = o1.read_text(encoding='utf-8-sig')
    p0 = style_line(txt)
    check('基准/画布换成 1440×1080', 'PlayResX: 1440' in txt and 'PlayResY: 1080' in txt,
          [ln for ln in txt.splitlines() if 'PlayRes' in ln])
    check('基准/同高时字号不动', p0[2] == '80', f'字号 {p0[2]}')
    check('基准/横边距按 x 缩', p0[6] == '45', f'MarginL {p0[6]}（60×0.75 应 45）')
    check('基准/纵边距不动', p0[8] == '20', f'MarginV {p0[8]}')

    # 缩放高度（1080p → 720p）：字号按 y 缩
    o2 = d / 'o2.ass'
    sn.cmd_base(src, (1280, 720), o2)
    p1 = style_line(o2.read_text(encoding='utf-8-sig'))
    check('基准/降到 720p 字号按 y 缩', p1[2] == '53', f'字号 {p1[2]}（80×0.667 应 53）')
    check('基准/720p 横边距也缩', p1[6] == '40', f'MarginL {p1[6]}（60×0.667 应 40）')

    # 反向：源基准 PlayRes 是 0 —— 没有坐标系就没法换算，必须拒绝而不是猜
    z = d / 'z.ass'
    z.write_bytes(src.read_bytes().replace(b'PlayResX: 1920', b'PlayResX: 0'))
    try:
        sn.cmd_base(z, (1440, 1080), d / 'z.out.ass')
        check('基准/源 PlayRes 0 要拒绝', False, '没拒绝，猜了坐标系')
    except sn.Bad:
        check('基准/源 PlayRes 0 要拒绝', True)

    # build_base 与样式器共用同一套缩放，不能各写一份
    check('基准/缩放函数只有一份', bb.rescale_styles is sn.rescale_styles, '两处各有一份')


def t_style():
    """文件层样式规范化：换画布要同步换算坐标，正文一个字都不许动。"""
    base = ('[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n'
            '[V4+ Styles]\n'
            'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, '
            'BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, '
            'BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
            'Style: Default,字体A,79,&H00FFFFFF,&H00000000,&H006C3300,&H00000000,-1,0,0,0,'
            '100,100,0,0,1,8,4,2,19,19,19,1\n'
            'Style: English,字体A,53,&H00FFFFFF,&H00000000,&H002F2F2F,&H00000000,0,0,0,0,'
            '100,100,0,0,1,8,4,2,19,19,19,1\n\n'
            '[Events]\nFormat: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n')
    src = ('[Script Info]\nScriptType: v4.00+\nPlayResX: 384\nPlayResY: 288\n\n'
           '[V4+ Styles]\n'
           'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, '
           'BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, '
           'BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n'
           'Style: Default,字体B,21,&H00FFFFFF,&H00000000,&H006C3300,&H00000000,-1,0,0,0,'
           '100,100,0,0,1,2,1,2,5,5,5,1\n\n'
           '[Events]\nFormat: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n'
           'Dialogue: 0,0:00:10.00,0:00:12.00,Default,,0,0,0,,{\\pos(192,274)\\fs16}标在画面上的字\n'
           'Comment: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,这条注释不许动\n')

    d = Path(tempfile.mkdtemp())
    bp, sp = d / 'base.ass', d / 'src.ass'
    bp.write_bytes(base.encode('utf-8'))
    sp.write_bytes(src.encode('utf-8'))                   # 故意不带 BOM

    out, st, warn, src_d = sn.apply_file(sp, bp)
    onepass = '\n'.join(out)
    check('样式/画布换基准', 'PlayResX: 1920' in onepass and 'PlayResY: 1080' in onepass, st['画布'])
    check('样式/样式表换基准', 'Style: Default,字体A,79' in onepass, '样式表没换成基准的')
    check('样式/pos 按轴换算', '\\pos(960,1027.5)' in onepass, onepass[onepass.find('\\pos'):][:40])
    check('样式/内联字号按高换算', '\\fs60' in onepass, onepass[onepass.find('\\fs'):][:20])
    check('样式/正文不动', '标在画面上的字' in onepass, '正文被改了')
    check('样式/Comment 行不动', '这条注释不许动' in onepass, 'Comment 被改了')
    check('样式/改比例要警告', any('画布比例变了' in w for w in warn), str(warn))

    dp = d / 'dst.ass'
    sn.write_out(out, src_d, dp, None)
    check('样式/不带 BOM 就不加 BOM', not sn.has_bom(dp), '凭空加了 BOM')
    # 换行符必须在解码后的文本上判：UTF-16 的 CRLF 是 \r\x00\n\x00，在原始字节里找
    # b'\r\n' 永远找不到。2026-11-07 拿法医的 UTF-16 源字幕撞出来——判成 LF 之后写回，
    # 整篇 CRLF 被悄悄改成 LF，复算还全绿。正反两条都锁住，改回字节判断会立刻红。
    t8 = dp.read_text(encoding='utf-8-sig')
    check('样式/LF 文件不改成 CRLF', '\r' not in t8, 'LF 源被写成 CRLF')
    u16 = d / 'u16.ass'
    u16.write_bytes(src.replace('\n', '\r\n').encode('utf-16'))
    d16 = sn.decode_of(u16)
    check('样式/UTF-16 的 CRLF 认得出来', d16['eol'] == '\r\n', repr(d16['eol']))
    out16, _s16, _w16, _d16 = sn.apply_file(u16, bp)
    p16 = d / 'u16.out.ass'
    sn.write_out(out16, _d16, p16, None)
    t16 = p16.read_bytes().decode('utf-16')
    check('样式/UTF-16 写回保住 CRLF',
          t16.count('\r\n') > 0 and '\n' not in t16.replace('\r\n', ''),
          f'CRLF {t16.count(chr(13) + chr(10))} / 孤立 LF {t16.replace(chr(13) + chr(10), "").count(chr(10))}')
    issues, st2 = sn.verify(sp, dp, bp, (1920, 1080))
    check('样式/复算通过', not issues and st2['标签不符'] == 0, str(issues))

    # 反向①：PlayRes 是 0 —— 没有坐标系可换算，必须拒绝，不许猜
    zero = d / 'zero.ass'
    zero.write_bytes(src.replace('PlayResX: 384', 'PlayResX: 0').replace('PlayResY: 288', 'PlayResY: 0')
                     .encode('utf-8'))
    try:
        sn.apply_file(zero, bp)
        check('样式/画布为 0 时拒绝', False, '没拒，等于猜了个比例')
    except sn.Bad:
        check('样式/画布为 0 时拒绝', True)

    # 反向②：偷改一条正文，复算要抓出来（定位到第 1 条，别拿整表判）
    bad = d / 'bad.ass'
    bad.write_bytes(('\n'.join(out)).replace('标在画面上的字', '标在画面上的字（改）').encode('utf-8'))
    issues2, st3 = sn.verify(sp, bad, bp)
    check('样式/复算抓正文被改', st3['正文变'] == 1 and any('正文变了' in x for x in issues2), str(issues2))

    # 反向③：改了画布却没换算坐标，要逐条报出来
    half = d / 'half.ass'
    half.write_bytes(src.replace('PlayResX: 384', 'PlayResX: 1920')
                     .replace('PlayResY: 288', 'PlayResY: 1080').encode('utf-8'))
    issues3, st4 = sn.verify(sp, half, bp)
    check('样式/抓「改画布没换算」', st4['标签不符'] >= 1, str(issues3))

    # 反向④：矢量裁切不动，但要计数报出来
    vec = d / 'vec.ass'
    vec.write_bytes(src.replace('\\pos(192,274)', '\\clip(m 0 0 l 100 0 100 100)').encode('utf-8'))
    _o, st5, _w, _d = sn.apply_file(vec, bp)
    check('样式/矢量裁切只报不改', st5['矢量裁切未动'] == 1, str(st5))

    # 反向⑤：旧样式名对不上基准 —— 必须拒绝。不拒的后果是整片静默回落 Default，一个字都不报
    ren = d / 'ren.ass'
    ren.write_bytes(src.replace('Dialogue: 0,0:00:10.00,0:00:12.00,Default',
                                'Dialogue: 0,0:00:10.00,0:00:12.00,*Default').encode('utf-8'))
    try:
        sn.apply_file(ren, bp)
        check('样式/旧样式名对不上就拒绝', False, '没拒，会静默回落 Default')
    except sn.Bad:
        check('样式/旧样式名对不上就拒绝', True)
    o2, _s2, _w2, _d2 = sn.apply_file(ren, bp, style_map={'*Default': 'Default'})
    line2 = next(x for x in o2 if x.startswith('Dialogue:'))
    check('样式/style-map 把引用一起改名', line2.split(',')[3] == 'Default', line2[:60])

    # 反向⑥：\move 的时间参数不许乘比例（第二部剧实测出来的假红）
    mv = d / 'mv.ass'
    mv.write_bytes(src.replace('\\pos(192,274)', '\\move(192,274,96,137,0,2000)').encode('utf-8'))
    o3, _s3, _w3, _d3 = sn.apply_file(mv, bp)
    txt3 = '\n'.join(o3)
    check('样式/move 坐标换算、时间不动', '\\move(960,1027.5,480,513.75,0,2000)' in txt3,
          txt3[txt3.find('\\move'):][:50])
    dp3 = d / 'mv.dst.ass'
    sn.write_out(o3, _d3, dp3, None)
    i3, _s4 = sn.verify(mv, dp3, bp)
    check('样式/复算不误报 move 时间', not i3, str(i3))

    # 反向⑦：\fscx/\fscy 是百分比、\frz 是角度，换画布一律原样不动。
    # 2026-11-07 人眼截 0:00:05 帧抓到：fscx105 被当 x 轴长度乘 5 变 525，
    # 声明字横向炸出画面；而当时复算「标签不符 0」全绿，因为 tag_values 与
    # scale_tags 共用同一个 TAG 分类。正反两条都锁住，改回去会立刻红。
    pct = d / 'pct.ass'
    pct.write_bytes(src.replace('\\pos(192,274)',
                                '\\pos(192,274)\\fscx105\\fscy90\\frz15').encode('utf-8'))
    o4, _s5, _w5, _d5 = sn.apply_file(pct, bp)
    txt4 = '\n'.join(o4)
    check('样式/fscx fscy frz 不换算',
          '\\fscx105\\fscy90\\frz15' in txt4 and '\\fscx525' not in txt4,
          txt4[txt4.find('\\fscx'):][:70])
    dp4 = d / 'pct.dst.ass'
    sn.write_out(o4, _d5, dp4, None)
    i4, _s6 = sn.verify(pct, dp4, bp)
    check('样式/复算不误报百分比标签', not i4, str(i4))

    # MF6：内联标签 → 样式引用。判据全文见 docs/工序-清稿-规则清单.md「MF6 具体怎么换」
    B = '\\'

    def mk(zh, blocks, tail):
        return zh + B + 'N' + blocks + tail

    six = ''.join('{' + B + t + '}' for t in
                  ('fn某字体', 'fs14', 'b0', 'c&HFFFFFF&', '3c&H2F2F2F&', '4c&H000000&'))
    o6, n6, why6 = sn.mf6(mk('就是今晚', six, '- What do you want?'))
    check('样式/MF6 六块换成一个引用',
          n6 == 1 and o6 == '就是今晚' + B + 'N{' + B + 'rEnglish}- What do you want?', o6[:60])
    check('样式/MF6 带数字的 b0/fs14 不算未知标签', not why6, str(why6))
    check('样式/MF6 英文以破折号开头也要认', n6 == 1, '漏了 - What 这类')
    check('样式/MF6 后接中文不动（中文折行）',
          sn.mf6(mk('前半句', '{' + B + 'fs16}', '后半句'))[1] == 0)
    check('样式/MF6 行首特效没有折行标记不动',
          sn.mf6('{' + B + 'an8' + B + 'fs16}字幕来源')[1] == 0)
    _o7, n7, why7 = sn.mf6(mk('画面上', '{' + B + 'fs16' + B + 'pos(100,200)}', 'On screen'))
    check('样式/MF6 带定位参数不换', n7 == 0 and 'pos' in str(why7), str(why7))
    check('样式/MF6 已合规不重复换',
          sn.mf6(mk('就是今晚', '{' + B + 'rEnglish}', 'Tonight'))[1] == 0)

    f6line = ('Dialogue: 0,0:00:10.00,0:00:12.00,Default,,0,0,0,,'
              + '就是今晚' + B + 'N' + six + 'Tonight\n')
    f6src = d / 'f6.ass'
    f6src.write_bytes((base + f6line).encode('utf-8'))
    _o8, _s8, _w8, _d8 = sn.apply_file(f6src, bp)                    # 默认不开
    check('样式/MF6 不给开关就不动标签', six in '\n'.join(_o8), '没开 --f6 却改了标签')
    _o9, _s9, _w9, _d9 = sn.apply_file(f6src, bp, f6=True)
    check('样式/MF6 开了就换成引用',
          B + 'N{' + B + 'rEnglish}Tonight' in '\n'.join(_o9), '\n'.join(_o9)[-90:])

    noen_txt = '\n'.join(l for l in base.split('\n') if not l.startswith('Style: English,'))
    noen = d / 'noen.ass'
    noen.write_bytes(noen_txt.encode('utf-8'))
    try:
        sn.apply_file(f6src, noen, f6=True)
        check('样式/MF6 基准没有英文样式就拒绝', False, '没拒，会静默回落 Default')
    except sn.Bad:
        check('样式/MF6 基准没有英文样式就拒绝', True)

    # 底纹条判据（F8 只报不删，但报得对不对要验）：可见字符只剩方块字符才算
    check('样式/底纹条候选认得出', bool(sn.BLOCKY.match('■■■')), '方块条没认出来')
    check('样式/正常台词不算底纹条', not sn.BLOCKY.match('这是正常台词'), '把正常台词判成底纹条')


def t_doclint_skills():
    """技能行数上限：超了必须报，没超不许报。

    为什么要这一条：`docs/文档落点表.md` 给技能写了「各自 ≤250」，但 `doc_lint` 原先只扫
    docs/——上限写在表里、没人查，就是一句空头承诺。这条回归盯的是「承诺可执行」本身。
    """
    import doc_lint as dl
    d = Path(tempfile.mkdtemp())
    (d / 'skills' / 'big').mkdir(parents=True)
    (d / 'skills' / 'ok').mkdir(parents=True)
    (d / 'skills' / 'big' / 'SKILL.md').write_text('\n'.join('x' * 20 for _ in range(251)) + '\n',
                                                   encoding='utf-8')
    (d / 'skills' / 'ok' / 'SKILL.md').write_text('\n'.join('x' * 20 for _ in range(250)) + '\n',
                                                  encoding='utf-8')
    res = dl.check_skills(d)
    errs = [m for tag, m in res if tag == 'ERROR']
    check('doc_lint/技能 251 行报 ERROR', len(errs) == 1, res)
    check('doc_lint/技能报错点到名', bool(errs) and 'big/SKILL.md' in errs[0], errs)
    check('doc_lint/技能 250 行不报（边界）', not any('ok/SKILL.md' in m for _, m in res), res)
    empty = Path(tempfile.mkdtemp())
    check('doc_lint/没有 skills 目录不炸', dl.check_skills(empty) == [])

    # 豁免得是「换基线」，不是「永久免检」——涨过基线必须重新报
    d2 = Path(tempfile.mkdtemp())
    (d2 / 'skills' / 'qc').mkdir(parents=True)
    f = d2 / 'skills' / 'qc' / 'SKILL.md'
    f.write_text('\n'.join('x' * 20 for _ in range(260)) + '\n', encoding='utf-8')
    old = dl.SKILL_EXEMPT
    dl.SKILL_EXEMPT = {'qc': (300, '测试用')}
    try:
        res2 = dl.check_skills(d2)
        check('doc_lint/豁免内不报 ERROR', not [m for t, m in res2 if t == 'ERROR'], res2)
        check('doc_lint/豁免内留一行 WARN',
              any(t == 'WARN' and '按豁免放行' in m for t, m in res2), res2)
        f.write_text('\n'.join('x' * 20 for _ in range(301)) + '\n', encoding='utf-8')
        res3 = dl.check_skills(d2)
        check('doc_lint/涨过豁免基线报 ERROR',
              any(t == 'ERROR' and '又涨' in m for t, m in res3), res3)
    finally:
        dl.SKILL_EXEMPT = old


def t_handoff_fresh():
    """接棒必读的同步检查：按 commit 比，不按日期比。

    2026-11-07 加。原版用 `--format=%cs` 只拿到日期，同一天改多少次都报「通过」：
    11-07 18:02–18:12 之间台账与判定记录改了 **6 次**、接棒必读一次没跟，检查跑两遍
    都过，而接棒必读里「实证仍待重建」那行当时已经是假的。**同一天内的落后是常态。**

    在临时仓库里造两种情形——同一次提交改（不许报）、台账单独领先（必须报）。
    """
    import doc_lint as dl
    import subprocess
    try:
        subprocess.run(['git', '--version'], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return                          # 没 git 就没这个场景，跳过不算失败

    d = Path(tempfile.mkdtemp())
    docs = d / 'docs'
    docs.mkdir()
    for name in ('接棒必读', '待办台账', '待办台账-详情', '判定记录'):
        body = '# %s\n\n> 最后更新：2026-11-07\n' % name if name == '接棒必读' else '# %s\n' % name
        (docs / (name + '.md')).write_text(body, encoding='utf-8')

    def git(*a):
        return subprocess.run(['git', *a], cwd=d, capture_output=True, text=True)

    git('init', '-q')
    git('config', 'user.email', 't@t.local')
    git('config', 'user.name', 't')
    git('add', '-A')
    git('commit', '-q', '-m', 'init')

    old_docs, old_root = dl.DOCS, dl.ROOT
    dl.DOCS, dl.ROOT = docs, d
    try:
        # ① 接棒必读与台账同一次提交改：正是要的状态，不许报
        for f in ('接棒必读.md', '待办台账.md'):
            p = docs / f
            p.write_text(p.read_text(encoding='utf-8') + '\n补一句\n', encoding='utf-8')
        git('add', '-A')
        git('commit', '-q', '-m', 'same')
        res = dl.check_handoff_doc()
        check('接棒/同一次提交不报落后',
              not [m for t, m in res if '比接棒必读新' in m], res)

        # ② 只改台账、另起一次提交：接棒必读落后一个 commit，必须报
        p = docs / '待办台账.md'
        p.write_text(p.read_text(encoding='utf-8') + '\n再加一条\n', encoding='utf-8')
        git('add', '-A')
        git('commit', '-q', '-m', 'only-taizhang')
        res = dl.check_handoff_doc()
        check('接棒/台账单独领先必须报',
              any(t == 'WARN' and '待办台账.md 的最近一次提交比接棒必读新' in m for t, m in res),
              res)

        # ③ 改了没提交也算「可能没跟」——工作区脏时不该安静通过
        p.write_text(p.read_text(encoding='utf-8') + '\n未提交的改动\n', encoding='utf-8')
        res = dl.check_handoff_doc()
        check('接棒/未提交改动要提醒',
              any('未提交的改动' in m for t, m in res), res)
    finally:
        dl.DOCS, dl.ROOT = old_docs, old_root


def t_scaffold():
    """骨架脚手架：建出来必须是个能过体检的库，而且不许覆盖别人的文件。

    这一条必须起子进程——它验的是「新库自己那份 doc_lint」跑不跑得过，
    在本进程里 import 进来的是本库这份，ROOT 已经指死了。
    """
    import subprocess
    here = Path(__file__).resolve().parent
    tool = here / 'scaffold.py'
    check('骨架/脚手架脚本在', tool.is_file(), str(tool))

    d = Path(tempfile.mkdtemp()) / '新库'
    r = subprocess.run([sys.executable, str(tool), str(d), '--name', '测试库'],
                       capture_output=True, text=True)
    out = (r.stdout or '') + (r.stderr or '')
    check('骨架/建库退出码 0', r.returncode == 0, out[-200:])
    for rel in ('docs/接棒必读.md', 'docs/待办台账.md', 'docs/判定记录.md',
                'docs/文档落点表.md', 'README.md', '.gitattributes',
                '.githooks/pre-commit', 'tools/doc_lint.py', 'tools/eol_guard.py'):
        check(f'骨架/有 {rel}', (d / rel).is_file())
    txt = (d / 'docs' / '接棒必读.md').read_text(encoding='utf-8')
    check('骨架/占位符替换干净', '{{' not in txt, txt[:60])
    check('骨架/日期是今天', datetime.date.today().isoformat() in txt)
    check('骨架/库名写进 README', '测试库' in (d / 'README.md').read_text(encoding='utf-8'))

    # 撞名不许覆盖：骨架是模板，不是「同步器」
    mine = d / 'README.md'
    mine.write_text('# 我自己改的\n', encoding='utf-8')
    subprocess.run([sys.executable, str(tool), str(d)], capture_output=True, text=True)
    check('骨架/撞名不覆盖', mine.read_text(encoding='utf-8') == '# 我自己改的\n',
          mine.read_text(encoding='utf-8')[:40])

    # --with-skills 带哪些文档：命名准则必须带（工序文档指它）、**手册必须带齐**（4 份技能正文
    # 都指着它，缺一份就是死链），实证一律不许出门（它记的是「哪一集踩了什么坑」，含真实集号）。
    # 2026-11-07 实测漏带过一份 `工序-审稿-实证-品控.md`——文件名以「-品控」结尾，
    # endswith(('-手册','-实证')) 拦不住它，那是红线。这条断言要一直留着。
    d2 = Path(tempfile.mkdtemp()) / '带技能库'
    r2 = subprocess.run([sys.executable, str(tool), str(d2), '--name', '测试库', '--with-skills'],
                        capture_output=True, text=True)
    out2 = (r2.stdout or '') + (r2.stderr or '')
    check('骨架/带技能建库退出码 0', r2.returncode == 0, out2[-200:])
    check('骨架/带技能时命名准则在', (d2 / 'docs' / '命名准则.md').is_file())
    check('骨架/带技能时工序文档在', (d2 / 'docs' / '工序-审稿.md').is_file())
    leaked = sorted(p.name for p in (d2 / 'docs').glob('*.md') if '实证' in p.name)
    check('骨架/实证不许带出门', not leaked, str(leaked))
    # 反向断言：手册必须带齐——4 份技能正文都指着它，缺一份就是死链（2026-11-07 改）
    missing_hb = sorted(p.name for p in (here.parent / 'docs').glob('工序-*-手册.md')
                        if not (d2 / 'docs' / p.name).is_file())
    check('骨架/手册必须带齐', not missing_hb, str(missing_hb))

    # 第二层死链：通用件里指向 docs/ 的指针必须都落地。
    # 2026-11-07 第二轮审计实地点出——只验技能正文的第一层指针不够：手册引用的
    # 判断纪律 / findings写法 / 中间产物格式 / 判据表当时都没带，技能引用的中间产物格式也没带。
    # 实证那几处**有意断**：它不进模板（含真实集号），新库自己攒，所以显式排除。
    import re
    pat = re.compile(r'docs/[A-Za-z0-9_\u4e00-\u9fa5./-]+\.(?:md|tsv)')
    gen = list((d2 / 'skills').glob('*/SKILL.md')) + list((d2 / 'docs').glob('工序-*.md'))
    gen += [d2 / 'docs' / n for n in ('命名准则.md', '判断纪律.md', 'findings写法.md',
                                      '中间产物格式.md', '流程总图.md', '派活单模板.md')]
    dead = set()
    for p in gen:
        if not p.is_file():
            continue
        for m in pat.finditer(p.read_text(encoding='utf-8-sig')):
            ref = m.group(0).rstrip('.,;):')
            if '实证' in ref or '<' in ref:
                continue
            if not (d2 / ref).is_file():
                dead.add(ref)
    check('骨架/通用件里无文档死链（实证除外）', not dead, str(sorted(dead)[:4]))

    # 裸引用：`references/` 这个目录早没了（技能重做 9→4 时删的）。通用件里再出现，
    # 就是让人去读一个不存在的东西——手册第 16 行自己写着「确实没有的直说『本库没有』，别留裸引用」。
    # 2026-11-07 第三轮审计：审稿手册两处 + 派活单模板两处，指的实物只在剧库旧技能残留里躺着。
    # **不按「裸 .md/.tsv 全量扫」**：实测那样报 38 类 70 处，全是运行时产物名（mech.tsv /
    # 补行.tsv / 片1.tsv），正则分不出「去读这个文件」和「产出这个文件」。
    bare = []
    for p in gen:
        if not p.is_file():
            continue
        for i, line in enumerate(p.read_text(encoding='utf-8-sig').splitlines(), 1):
            if 'references/' in line or 'references\\' in line:
                if any(w in line for w in ('本库没有', '已删', '一起删', '残留')):
                    continue
                bare.append(f'{p.name}:{i}')
    check('骨架/通用件里无 references 裸引用', not bare, str(bare[:4]))


def t_terms():
    """术语候选：别把 \\N 挑成 N、别把称谓当专名、长词碎片要合并。"""
    txt = '{\\fs20}你好 首相\\N{\\rEnglish}Hello, Prime Minister.'
    c = tc.candidates_of(txt)
    check('术语/换行符不变成候选', 'N' not in c, str(sorted(c))[:60])
    check('术语/多词候选认得出', 'Prime Minister' in c, str(sorted(c))[:60])

    c2 = tc.candidates_of('Mr Smith is here.')
    check('术语/称谓本身不列', 'Mr' not in c2 and 'Smith' in c2, str(sorted(c2))[:60])

    c3 = tc.candidates_of('Well the Minister I believe.')
    check('术语/半句不成串', not any(' ' in x and x.endswith(' I') for x in c3), str(sorted(c3))[:60])

    rows = [('Prime', {'n': 10, 'first': 'x:1', 'zh': ''}),
            ('Prime Minister', {'n': 9, 'first': 'x:1', 'zh': ''}),
            ('Minister', {'n': 30, 'first': 'x:1', 'zh': ''})]
    kept, dropped = tc.drop_fragments(rows)
    names = [n for n, _ in kept]
    check('术语/长词碎片被略去', 'Prime' not in names and 'Minister' in names, str(names))
    check('术语/碎片计数对得上', dropped == 1, f'略去 {dropped} 条')


def t_land_inserts():
    """补行：位置按时间算、after_no 只核对、verify 在条号漂移后仍能对位。"""
    head = ('[Events]\nFormat: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n'
            'Dialogue: 0,0:00:40.30,0:00:41.03,Default,NTP,0,0,0,,就是今晚\\N{\\rEnglish}Tonight\'s the night.\n'
            'Dialogue: 0,0:00:50.00,0:00:51.00,Default,NTP,0,0,0,,别动这一条\\N{\\rEnglish}Leave this one.\n')
    d = Path(tempfile.mkdtemp())
    src = d / 'i.ass'
    src.write_bytes(head.encode('utf-8'))

    ins_tsv = d / 'ins.tsv'
    ins_tsv.write_text('start\tend\tcn\ten\tafter_no\tstyle\n'
                       '0:00:45.00\t0:00:46.00\t漏掉的一句\tThe missing line.\t1\t\n',
                       encoding='utf-8', newline='')
    ins = lv.read_inserts(ins_tsv)
    check('补行/读表条数=1', len(ins) == 1 and ins[0]['after_no'] == 1, str(ins))

    out, done, miss, added = lv.apply(src, [], ins)
    check('补行/实补=1', added == 1 and done == 0 and not miss, f'added={added} miss={miss}')
    check('补行/行数=原+1', len(out) == len(head.splitlines()) + 1, f'{len(out)}')
    # 位置断言要定位到那一条，不能拿整份文件做 in 判断
    want = ('Dialogue: 0,0:00:45.00,0:00:46.00,Default,NTP,0,0,0,,'
            '漏掉的一句\\N{\\rEnglish}The missing line.')
    check('补行/插在锚点条之后', out[3].rstrip('\r\n') == want, out[3][:80])
    check('补行/原条没被挤动', out[4].rstrip('\r\n') == head.splitlines()[3], out[4][:60])
    check('补行/样式抄锚点', 'Default,NTP' in out[3], out[3][:60])

    dst = d / 'i2.ass'
    dst.write_bytes(''.join(out).encode(lv.encoding_of(src)))
    issues, st = lv.verify(src, dst, [], ins)
    check('补行/复算通过', not issues and st['补行'] == 1 and st['多改'] == 0, str(issues))

    # 反向①：after_no 与时间算出来的位置不一致 → 拒。老库跳行事故就是这个形状，
    # 只是那边人填 after_ln、没人核；这里必须硬拦，不许按声明的位置插。
    bad_after = d / 'ins2.tsv'
    bad_after.write_text('0:00:45.00\t0:00:46.00\t漏掉的一句\tThe missing line.\t2\t\n',
                         encoding='utf-8', newline='')
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            lv.apply(src, [], lv.read_inserts(bad_after))
        check('补行/after_no 对不上要拒', False, '没拒，声明的位置会把补行插错地方')
    except SystemExit as e:
        check('补行/after_no 对不上要拒', e.code == 2, f'退出码应是 2，实际 {e.code}')

    # 反向②：新行落在锚点条的显示区间内 → 同屏叠字，拒
    overlap = d / 'ins3.tsv'
    overlap.write_text('0:00:40.50\t0:00:41.50\t撞上了\n', encoding='utf-8', newline='')
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            lv.apply(src, [], lv.read_inserts(overlap))
        check('补行/撞进上一条区间要拒', False, '没拒，会和上一条同屏叠字')
    except SystemExit as e:
        check('补行/撞进上一条区间要拒', e.code == 2, f'退出码应是 2，实际 {e.code}')

    # 反向③：补行同一句声明两次 → 拒（补两遍不会得到两条，只会让条数对不上）
    dup = d / 'ins4.tsv'
    dup.write_text('0:00:45.00\t0:00:46.00\t漏掉的一句\t\t1\t\n'
                   '0:00:45.00\t0:00:46.00\t漏掉的一句\t\t1\t\n', encoding='utf-8', newline='')
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            lv.apply(src, [], lv.read_inserts(dup))
        check('补行/重复声明要拒', False, '没拒，同一条补行声明了两次')
    except SystemExit as e:
        check('补行/重复声明要拒', e.code == 2, f'退出码应是 2，实际 {e.code}')

    # 反向④：偷改补行的时间 → verify 必须抓（内容对、时间错，播放器会跳）
    shift = d / 'i3.ass'
    shift.write_bytes(''.join(out).replace('0:00:45.00,0:00:46.00', '0:00:44.00,0:00:46.00')
                      .encode(lv.encoding_of(src)))
    issues3, _st3 = lv.verify(src, shift, [], ins)
    check('补行/复算抓时间被改', any('时间不对' in x for x in issues3), str(issues3))

    # 反向⑤：插了补行之后，偷改一条没声明的原条 → 仍要抓到「多改」。
    # 这条专门锁新对位逻辑：条号整体后移后，按下标硬套会漏报。
    dirty = d / 'i4.ass'
    dirty.write_bytes(''.join(out).replace('别动这一条', '偷改这一条').encode(lv.encoding_of(src)))
    issues4, st4 = lv.verify(src, dirty, [], ins)
    check('补行/条号漂移后仍抓多改', st4['多改'] == 1 and any('未声明' in x for x in issues4), str(issues4))

    # 反向⑥：SRT 带补行 → 拒（块结构要重排序号，本版不做，不许静默照插）
    srt = tmp_srt('1\n00:00:01,000 --> 00:00:02,000\n第一句\n\n2\n00:00:05,000 --> 00:00:06,000\n第二句\n\n')
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            lv.apply(srt, [], lv.read_inserts(ins_tsv))
        check('补行/SRT 要拒', False, '没拒，SRT 会被插坏结构')
    except SystemExit as e:
        check('补行/SRT 要拒', 'SRT' in str(e.code) or e.code == 2, f'退出码 {e.code}')

    # 改字与补行同时来：声明条号一律按原文件算，补行插在改动之后
    both = d / 'ins5.tsv'
    both.write_text('0:00:45.00\t0:00:46.00\t漏掉的一句\t\t1\t\n', encoding='utf-8', newline='')
    ch = d / 'ch.tsv'
    ch.write_text('1\t就是今晚\t正是今晚\n2\t别动这一条\t别动这条\n', encoding='utf-8', newline='')
    out5, done5, miss5, added5 = lv.apply(src, lv.read_changes(ch), lv.read_inserts(both))
    check('补行/改动与补行同批', done5 == 2 and added5 == 1 and not miss5,
          f'done={done5} added={added5} miss={miss5}')
    check('补行/同批后补行仍在条1之后', '漏掉的一句' in out5[3] and '正是今晚' in out5[2], out5[3][:60])


def t_text_scan():
    """文本层扫描：只做唯一改法；标签、花括号、折行一律先挡，不赌。

    反例是重点：数字千分位、数字与中文相邻、带参数花括号、中文 \\N 折行——
    这四类机器看着都像「该改」，改错了没人看得出来。
    """
    head = ('[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n'
            '[V4+ Styles]\nFormat: Name, Fontname, Fontsize\nStyle: Default,Arial,21\n\n'
            '[Events]\nFormat: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n')

    def mk(texts):
        d = Path(tempfile.mkdtemp())
        p = d / 't.ass'
        body = ''.join(f'Dialogue: 0,0:00:0{i}.00,0:00:0{i}.90,Default,,0,0,0,,{t}\\N{{\\rEnglish}}Line {i}.\n'
                       for i, t in enumerate(texts, 1))
        p.write_bytes((head + body).encode('utf-8'))
        return p

    # 正例：半角标点贴中文；中英之间缺空格
    # 反例：数字千分位、数字与中文相邻——都不是「中英之间」
    p = mk(['谁的电话?', '共有21,793票', '我用BBC的东西', '2万1千793票'])
    rows, _, _ = ts.scan(p, [])
    d = {r[0]: r for r in rows}
    check('文本层/半角问号换全角', d.get(1, ('', '', ''))[2] == '谁的电话？', str(d.get(1)))
    check('文本层/千分位逗号不动', 2 not in d, str(d.get(2)))
    check('文本层/中英之间补空格', d.get(3, ('', '', ''))[2] == '我用 BBC 的东西', str(d.get(3)))
    check('文本层/数字与中文不补空格', 4 not in d, str(d.get(4)))

    # MT2：plain 会先抹掉标签，所以必须靠 head_raw 才看得见——首版就是这里静默失效
    rows2, _, _ = ts.scan(mk(['<i>这句话</i>要删标签']), [])
    check('文本层/HTML 标签被删', bool(rows2) and 'MT2' in rows2[0][3] and '<i>' not in rows2[0][2],
          str(rows2))

    rows3, holds3, _ = ts.scan(mk(['你好{}世界']), [])
    check('文本层/空花括号被删', bool(rows3) and 'MT4' in rows3[0][3] and '{' not in rows3[0][2],
          str(rows3))

    rows4, holds4, _ = ts.scan(mk(['这是{\\c&H0000FF&}红色的字']), [])
    check('文本层/带参数花括号不改', not rows4 and any('花括号' in h[2] for h in holds4), str(holds4))

    rows5, holds5, _ = ts.scan(mk(['第一行\\N第二行']), [])
    check('文本层/中文折行不改', not rows5 and any('折行' in h[2] for h in holds5), str(holds5))

    # MT1：词表第三列；单字错写只报不改
    d1 = Path(tempfile.mkdtemp())
    tsv = d1 / 'term.tsv'
    tsv.write_text('原文\t规定译法\t已见错写法\nAlderman\t奥尔德曼\t奥德曼\n', encoding='utf-8')
    rows6, _, _ = ts.scan(mk(['他叫奥德曼']), ts.load_terms(tsv))
    check('文本层/词表错写法被改', bool(rows6) and rows6[0][2] == '他叫奥尔德曼', str(rows6))

    # 替换结果不能再被扫一遍：伯拿 / 伯纳 都映射到伯纳德，两遍扫描会造「伯纳德德」
    tsv4 = d1 / 'term4.tsv'
    tsv4.write_text('原文\t规定译法\t已见错写法\nBernard\t伯纳德\t伯拿|伯纳\n', encoding='utf-8')
    rows_dup, _, _ = ts.scan(mk(['请叫我伯拿 谢谢']), ts.load_terms(tsv4))
    check('文本层/替换结果不被二次替换',
          bool(rows_dup) and rows_dup[0][2] == '请叫我伯纳德 谢谢', str(rows_dup))

    tsv2 = d1 / 'term2.tsv'
    tsv2.write_text('原文\t规定译法\t已见错写法\nXxx\t甲\t乙\n', encoding='utf-8')
    rows7, holds7, _ = ts.scan(mk(['这是乙啊']), ts.load_terms(tsv2))
    check('文本层/单字错写只报不改', not rows7 and any('单字' in h[2] for h in holds7), str(holds7))

    tsv3 = d1 / 'term3.tsv'
    tsv3.write_text('原文\t规定译法\t已见错写法\nAaa\t甲\t奥德曼\nBbb\t乙\t奥德曼\n', encoding='utf-8')
    try:
        ts.load_terms(tsv3)
        dup_ok = False
    except SystemExit as e:
        dup_ok = (e.code == 2)
    check('文本层/错写法重复登记拒绝加载', dup_ok)

    # SRT 直接拒：文本层排在文件层之后，那时输入已经是 ASS
    try:
        ts.scan(tmp_srt('1\n00:00:01,000 --> 00:00:02,000\n中文\n\n'), [])
        srt_ok = False
    except SystemExit as e:
        srt_ok = (e.code == 2)
    check('文本层/SRT 直接拒', srt_ok)

    # 闭环：清单喂给落地工具，锚点必须对得上（口径漂了这里先炸）
    p8 = mk(['谁的电话?'])
    rows8, _, _ = ts.scan(p8, [])
    mtsv = Path(tempfile.mkdtemp()) / 'mech.tsv'
    ts.write_tsv(mtsv, ['no', 'old', 'new', 'rule', 'note'], rows8)
    out, done, miss, added = lv.apply(p8, lv.read_changes(mtsv))
    check('文本层/清单能被落地工具吃下', done == 1 and not miss, f'done={done} miss={miss}')
    p9 = Path(tempfile.mkdtemp()) / 'out.ass'
    p9.write_bytes(''.join(out).encode('utf-8'))
    _, cues9, _ = lv.load(p9)
    check('文本层/落地后文本是改后版本', cues9[0]['zh'] == '谁的电话？', cues9[0]['zh'])


def t_ass_rows():
    """ass_rows：拆中英、行号、拆不开要报警。"""
    import ass_rows
    d = Path(tempfile.mkdtemp())
    try:
        # 形态一：中文\\N{\\rEnglish}英文 —— 应该拆开
        f1 = d / 'a.ass'
        f1.write_text(
            '[Script Info]\nPlayResX: 1920\n\n[V4+ Styles]\n'
            'Style: Default,思源,52,1,0\n\n'
            '[Events]\n'
            'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
            'Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,你好\\N{\\rEnglish}Hello\n'
            'Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,第二条\\N{\\rEnglish}Second\n',
            encoding='utf-8')
        rows = ass_rows.iter_rows(f1)
        check('ass_rows：条数对', len(rows) == 2, str(len(rows)))
        check('ass_rows：中文拆出来', rows[0]['cn'].strip() == '你好', repr(rows[0]['cn']))
        check('ass_rows：英文拆出来', rows[0]['en'].strip() == 'Hello', repr(rows[0]['en']))
        check('ass_rows：行号是物理行号且 1 起', rows[0]['ln'] > 1 and rows[0]['ln'] < rows[1]['ln'],
              f"{rows[0]['ln']},{rows[1]['ln']}")
        check('ass_rows：时间码原文与秒数一致', rows[0]['start'] == '0:00:01.00'
              and abs(rows[0]['start_s'] - 1.0) < 1e-6, rows[0]['start'])

        # 形态二：内联标签后直接跟英文 —— 拆不开，必须报警（stderr）
        f2 = d / 'b.ass'
        f2.write_text(
            '[Script Info]\n\n[Events]\n'
            'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'
            'Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,中文行\\N{\\fn思源}It is a test line\n',
            encoding='utf-8')
        buf = io.StringIO()
        old = sys.stderr
        sys.stderr = buf
        try:
            rows2 = ass_rows.iter_rows(f2)
        finally:
            sys.stderr = old
        check('ass_rows：拆不开的形态会报警', '分隔符多半不是' in buf.getvalue(), buf.getvalue()[:60])
        check('ass_rows：拆不开时英文为空', not any(r['en'] for r in rows2), '')

        # ep_of 两种命名
        check('ass_rows：ep_of 认 S01E05', ass_rows.ep_of('x.S01E05.ass') == '01x05',
              ass_rows.ep_of('x.S01E05.ass'))
        check('ass_rows：ep_of 认 01x05', ass_rows.ep_of('x.01x05.ass') == '01x05',
              ass_rows.ep_of('x.01x05.ass'))
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


def t_check_rules():
    """判据编号闸：四条用例，三条专挑它该拒的形态。"""
    def run(argv):
        old = sys.argv
        sys.argv = ['check_rules.py'] + argv
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = cr.main()
        finally:
            sys.argv = old
        return code, buf.getvalue()

    ok = tmp_srt('no\told\tnew\trule\n1\t他来了\t他没有来\tC1.1\n2\t不知道\t不知道\tC2.1\n', 'a.tsv')
    code, out = run([str(ok)])
    check('check_rules：编号合规退出 0', code == 0, out)

    noc1 = tmp_srt('no\told\tnew\trule\n1\t不知道\t不知道\tC2.1\n', 'b.tsv')
    code, out = run([str(noc1)])
    check('check_rules：C1 为零退出 1', code == 1 and 'C1 组一条都没有' in out, out)

    bad = tmp_srt('no\told\tnew\trule\n1\t他来了\t他没有来\tC1.9\n', 'c.tsv')
    code, out = run([str(bad)])
    check('check_rules：编号不在表里退出 1', code == 1 and 'C1.9' in out, out)

    empty = tmp_srt('no\told\tnew\trule\n1\t他来了\t他没有来\tC1.1\n2\t他\t他\t\n', 'd.tsv')
    code, out = run([str(empty)])
    check('check_rules：rule 为空退出 1', code == 1 and 'rule 是空的' in out, out)

    # 用得极窄：12 条全挤在 C1.1 上——不判失败（可能真就只有这一类），但必须提示
    rows = ''.join('%d\t甲%d\t乙%d\tC1.1\n' % (i, i, i) for i in range(1, 13))
    narrow = tmp_srt('no\told\tnew\trule\n' + rows, 'n.tsv')
    code, out = run([str(narrow)])
    check('check_rules：用得极窄要提示', code == 0 and '只用到 1 种判据' in out, out)
    check('check_rules：F 为零只提示不失败', code == 0 and 'F 类' in out, out)


def t_land_inplace():
    """落地 --in-place：写盘后必须还能自证。

    2026-11-08 实测踩到：in-place 时 dst 就是 src，写完再 verify(src, dst) 等于拿改后
    比改后，必报满「旧文本在改前就找不到」——文件明明改对了，结论却是打回。此前 224 条
    自检全绿，因为全部是手动调 apply + 单独写 dst，从没走过 main() 的 in-place 分支。
    """
    import subprocess
    ass = ('[Script Info]\nScriptType: v4.00+\n\n[Events]\n'
           'Format: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n'
           "Dialogue: 0,0:00:40.30,0:00:41.03,Default,NTP,0,0,0,,就是今晚\\N{\\rEnglish}Tonight.\n")
    d = Path(tempfile.mkdtemp())
    src, ch = d / 'a.ass', d / 'c.tsv'
    src.write_bytes(ass.encode('utf-8'))
    ch.write_text('条号\t旧文本\t新文本\n1\t就是今晚\t正是今晚\n', encoding='utf-8', newline='')

    r = subprocess.run([sys.executable, str(TOOLS / 'land_verify.py'), 'apply',
                        str(src), str(ch), '--in-place'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    check('落地in-place/退出码 0', r.returncode == 0, (r.stdout + r.stderr).strip()[:220])
    check('落地in-place/写盘后自证通过', '在位 1' in r.stdout and '不符 0' in r.stdout, r.stdout.strip()[:220])
    check('落地in-place/中文已换', '正是今晚' in src.read_text(encoding='utf-8'), '文件没变')
    left = [p.name for p in d.iterdir() if '.before' in p.name]
    check('落地in-place/不留临时副本', not left, str(left))

    # 后缀不可靠：备份叫 .bak 也得按内容认出来，否则「拿备份重验」这条路是死的
    _l, cues, kind = lv.load(d / 'a.ass.bak')
    check('落地/备份按内容认 ASS', kind == 'ass' and len(cues) == 1, f'{kind} {len(cues)}')


def t_mk_meta():
    """mk_meta：分片行数与源对得上才产 meta，对不上就拒。

    口径要点：`meta.rows` = **各片行数相加，不含 skipped**。2026-11-08 写成源总条数，
    check_slices 当场报「总行数对不上：各片相加=913，meta.rows=973」。
    """
    import json
    import subprocess
    ass = ('[Script Info]\nScriptType: v4.00+\n\n[Events]\n'
           'Format: Layer, Start, End, Style, Actor, MarginL, MarginR, MarginV, Effect, Text\n'
           "Dialogue: 0,0:00:01.00,0:00:02.00,Default,NTP,0,0,0,,一\\N{\\rEnglish}One.\n"
           "Dialogue: 0,0:00:03.00,0:00:04.00,Default,NTP,0,0,0,,二\\N{\\rEnglish}Two.\n"
           "Dialogue: 0,0:00:05.00,0:00:06.00,Default,NTP,0,0,0,,三\\N{\\rEnglish}Three.\n")
    d = Path(tempfile.mkdtemp())
    src = d / 'x.S01E02.ass'
    src.write_bytes(ass.encode('utf-8'))

    def slice_file(name, n, first):
        body = ['行号\t时间码\t中文\t英文'] + ['%d\t0:00:01.00\t字\tw' % (first + i) for i in range(n)]
        (d / name).write_text('\n'.join(body) + '\n', encoding='utf-8-sig')

    def run(*extra):
        return subprocess.run([sys.executable, str(TOOLS / 'mk_meta.py'), str(d), str(src)] + list(extra),
                              capture_output=True, text=True, encoding='utf-8', errors='replace')

    slice_file('片1.tsv', 2, 6)          # 源 3 条，片1 拿 2 条 → skipped 1
    r = run('--force')
    meta = json.loads((d / 'meta.json').read_text(encoding='utf-8'))
    check('mk_meta/退出码 0', r.returncode == 0, (r.stdout + r.stderr).strip()[:200])
    check('mk_meta/rows 是各片相加', meta['rows'] == 2, f"rows={meta['rows']}（应是各片相加 2，不是源 3）")
    check('mk_meta/差额进 skipped', meta['skipped'] == {'未归类': 1}, str(meta['skipped']))
    check('mk_meta/自动认集号', meta['ep'] == 'S01E02', meta['ep'])

    # 已有 meta.json 不加 --force 就不许覆盖
    r2 = run()
    check('mk_meta/不覆盖已有文件', r2.returncode != 0, f'退出码 {r2.returncode}')

    # 跳过明细合计对不上 → 拒
    r3 = run('--force', '--skip', 'a=5')
    check('mk_meta/跳过明细对不上就拒', r3.returncode != 0, f'退出码 {r3.returncode}')

    # 各片相加超过源 → 拒（片切错了或源给错了）
    slice_file('片2.tsv', 5, 10)
    r4 = run('--force')
    check('mk_meta/片比源还多就拒', r4.returncode != 0, f'退出码 {r4.returncode}')
    (d / '片2.tsv').unlink()



def t_findings2tsv():
    """findings → 改动表：只出能落地的，evidence 必须搬过来（2026-11-08 新建工具）。"""
    import json
    import subprocess
    here = Path(__file__).resolve().parent
    tool = here / 'findings2tsv.py'
    d = Path(tempfile.mkdtemp())
    data = {'season': '01', 'episode': '01x01', 'findings': [
        {'ln': 2, 'cn': '原句甲', 'cn_new': '改后甲', 'evidence': '官方第 2 条', 'source_model': 'C1.1'},
        {'ln': 3, 'cn': '原句乙', 'cn_new': '', 'evidence': 'x', 'source_model': 'C1.5'},
        {'ln': 4, 'cn': '原句丙', 'cn_new': '原句丙', 'en_after': 'New', 'source_model': 'DR1'}],
        'inserts': [{'after_no': 5, 'cn': '补的', 'en': 'Ins'}]}
    src = d / 'f.json'
    src.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    out, ins = d / 'all.tsv', d / 'ins.tsv'
    r = subprocess.run([sys.executable, str(tool), str(src), '--tsv', str(out), '--ins', str(ins)],
                       capture_output=True, text=True)
    check('findings2tsv/退出码 0', r.returncode == 0, (r.stderr or '')[-160:])
    rows = [l.split('\t') for l in out.read_text(encoding='utf-8').strip().splitlines()] if out.is_file() else []
    check('findings2tsv/表头带 evidence', bool(rows) and rows[0] == ['no', 'old', 'new', 'rule', 'evidence'],
          str(rows[:1]))
    check('findings2tsv/只出可落地的 1 条', len(rows) == 2, str(len(rows)))
    check('findings2tsv/evidence 搬了过来', len(rows) > 1 and rows[1][4] == '官方第 2 条',
          str(rows[1:2]))
    check('findings2tsv/补行表 1 条',
          ins.is_file() and len(ins.read_text(encoding='utf-8').strip().splitlines()) == 2)
    check('findings2tsv/英文轨那条挑出来交人工', '[人工]' in (r.stdout or ''))


def t_cross_lib():
    """跨库用法必须有话说，不许静默通过（2026-11-08 加，`GQ-12` / `GQ-13` / `GQ-18`）。

    三处毛病是同一个形状：**输出是绿的，实际什么都没查**。
      ① `doc_lint` 没有版本号，拷进老库的副本过期无声，跑出来照样「没有 ERROR」；
      ② `doc_lint --docs` 指到本库之外原来直接抛 ValueError 崩掉；
      ③ `eol_guard` 跨库调用时文件全在 ROOT 之外，全部跳过，老版本仍打印「全部一致」、
         退出码 0 —— 一个没查的文件按「一致」报，人不会怀疑，这条最贵。
    这三条都得起子进程：验的是退出码和终端输出，不是函数返回值。
    """
    import subprocess
    here = Path(__file__).resolve().parent
    outside = Path(tempfile.mkdtemp())

    # ① 版本号：常量存在，且输出末尾带出来
    import doc_lint as dl
    check('跨库/版本常量存在', bool(getattr(dl, 'VERSION', '')), getattr(dl, 'VERSION', None))
    r = subprocess.run([sys.executable, str(here / 'doc_lint.py')],
                       capture_output=True, text=True)
    out = (r.stdout or '') + (r.stderr or '')
    check('跨库/doc_lint 输出带版本', f'doc_lint v{dl.VERSION}' in out, out[-120:])

    # ② 指到库外：说人话拒绝、退出码 2，不许把栈甩给用户
    r = subprocess.run([sys.executable, str(here / 'doc_lint.py'), '--docs', str(outside)],
                       capture_output=True, text=True)
    out = (r.stdout or '') + (r.stderr or '')
    check('跨库/doc_lint 拒绝库外目录（退出码 2）', r.returncode == 2, r.returncode)
    check('跨库/拒绝时给话说', '本库之外' in out, out[-200:])
    check('跨库/拒绝时不抛栈', 'Traceback' not in out, out[-200:])

    # ③ eol_guard 一个都没查：退 2，且不许出现「全部一致」
    (outside / 'a.md').write_text('# 测试\n', encoding='utf-8')
    r = subprocess.run([sys.executable, str(here / 'eol_guard.py'), '--check', str(outside)],
                       capture_output=True, text=True)
    out = (r.stdout or '') + (r.stderr or '')
    check('跨库/eol_guard 全跳过退 2', r.returncode == 2, r.returncode)
    check('跨库/eol_guard 不说「一致」', '全部一致' not in out, out[-200:])
    check('跨库/eol_guard 明说「没查」', '没查' in out, out[-200:])

    # ④ 反过来：本库正常路径不许被这次改动误伤，检查数必须大于 0
    r = subprocess.run([sys.executable, str(here / 'eol_guard.py'), '--check',
                        str(here.parent / 'docs')], capture_output=True, text=True)
    out = (r.stdout or '') + (r.stderr or '')
    check('跨库/本库正常跑退 0', r.returncode == 0, out[-200:])
    check('跨库/本库检查数 > 0', '检查 0 个' not in out, out[-200:])


def main():
    global VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()
    VERBOSE = a.verbose

    t_probe()
    t_align()
    t_triage()
    t_pairs()
    t_land()
    t_land_inserts()
    t_findings2tsv()
    t_land_inplace()
    t_mk_meta()
    t_accept()
    t_base()
    t_base_make()
    t_style()
    t_terms()
    t_text_scan()
    t_scaffold()
    t_doclint_skills()
    t_handoff_fresh()
    t_ass_rows()
    t_check_rules()
    t_cross_lib()

    print(f'\n通过 {len(PASS)}，失败 {len(FAIL)}')
    if FAIL:
        for n in FAIL:
            print(f'  失败：{n}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
