#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-06（新建：把散在各工序「完成标志」里的验收数字收成一条命令）
"""accept.py -- 验收：一条命令报出这集该对的全部数字，不用翻四份文档对账

为什么要有它：各工序的「完成标志」写着不同的数——清稿要「实改 = 声明、跳过 0」，
审稿要「档位声明过、密度落在 1.8–7.0 条／百条」——但没有人把它们列在一起。
于是每次验收都靠人记「还有哪项没看」，记漏了不报错，代价由下一集付。

**它只报数，不判内容。** 改动与补行那两块直接调 `land_verify.verify()`，判据只有一份；
本文件新增的只有四项：时间倒序、落盘密度（审稿改动 / 格式层动作分开报）、三数对账、定档痕迹。三档结论：
  ✓ 过     ✗ 不过（退出码 1）    ⚠ 要人看一眼（数字落在参考区间外，未必是错）

用法：
    python tools/accept.py <改前> <改后> [--changes 改动.tsv] [--inserts 补行.tsv] [--stage 定档.md]
    python tools/accept.py <改前> <改后> --changes 改动.tsv --findings 句法遍.tsv --findings 逐词遍.tsv
    python tools/accept.py <成品>                      # 只做结构体检（不传改前改后时同文件对自身）

「档位声明过」「清单逐条读完」这类**机器判不了**，本工具只查痕迹在不在（定档.md 有没有），
判不了就明写「机器判不了」，不许默默算它过关。

退出码：0 全过；1 有 ✗；2 参数/输入错
"""
import argparse
import sys
from pathlib import Path

import land_verify as lv

# 审稿第 10 节给的参考区间。只用来标 ⚠，不用来判 ✗——
# 文档原话是「超出要解释」，不是「超出就错」，工具不该比判据更严。
DENSITY_LO, DENSITY_HI = 1.8, 7.0


def inserts_position_ok(after_cues, ins):
    """补行在成品里的前后邻居，时间上要夹住它。**只查补行，不查全文件行序。**

    为什么不查全文件：ASS 的行序本来就不必等于时间序。实测某一集原成品——第 3、4 条是
    2:04 的片头标题卡，第 5 条却回到 0:11 的前情提要，因为那批卡被人为置顶。第一版按「后一条
    不得早于前一条」查全文件，当场在这份**交付过的真成品**上报了一处假红。
    要查的是「补行有没有插到邻居外面」，那才是补行会造成的错位（老库 #118：19:31 后面跟着 19:06）。
    """
    bad = []
    for i in ins:
        pos = next((k for k, c in enumerate(after_cues) if c.get('s') == i['s']), None)
        if pos is None:
            bad.append(f'{i["start"]} 成品里找不到这条')
            continue
        prev = after_cues[pos - 1] if pos else None
        nxt = after_cues[pos + 1] if pos + 1 < len(after_cues) else None
        if prev and prev.get('s') is not None and prev['s'] > i['s']:
            bad.append(f'{i["start"]} 的前一条（第 {prev["no"]} 条）比它晚')
        if nxt and nxt.get('s') is not None and nxt['s'] < i['s']:
            bad.append(f'{i["start"]} 的后一条（第 {nxt["no"]} 条）比它早')
    return bad


def stage_trace(path):
    """定档痕迹：只是「这份文件在不在、有没有档位两个字」，内容机器判不了。"""
    if not path:
        return None, '没给 --stage，档位是否声明过：机器判不了'
    p = Path(path)
    if not p.is_file():
        return False, f'定档文件不存在：{p}'
    txt = p.read_text(encoding='utf-8', errors='replace')
    if '档' not in txt:
        return False, '定档文件在，但里面没有「档」字——像是还没写'
    return True, '定档文件在'


def dialogue_count(lines):
    """数 Dialogue 行。整体验收判它，不判总行数——头部与样式表本来就会变。"""
    return sum(1 for ln in lines if ln.lstrip().lower().startswith('dialogue:'))


def style_count(lines):
    """数 [V4+ Styles] 段里的 Style 行。用来解释「行数为什么变了」。"""
    n, inside = 0, False
    for ln in lines:
        s = ln.strip()
        low = s.lower().replace(' ', '')
        if low == '[v4+styles]':
            inside = True
            continue
        if inside:
            if s.startswith('[') and s.endswith(']'):
                break
            if s.lower().startswith('style:'):
                n += 1
    return n


def count_findings(path):
    """数一份 findings 的条数（按行，表头不算）。**数不出来就明说，不猜。**

    判据只认「首列是数字或时间码」的行——findings 的字段各库不同，认列名等于替它定格式；
    认首列能认出的表足够用，认不出时返回 None，让调用方把它当「本轮没做三数对账」。
    """
    txt = Path(path).read_text(encoding='utf-8', errors='replace')
    n = 0
    for i, line in enumerate(txt.splitlines()):
        s = line.strip()
        if not s:
            continue
        first = s.split('\t')[0].strip()
        if first.isdigit() or ':' in first:
            n += 1
        elif i == 0:
            continue                      # 表头
        else:
            return None                   # 有一行既不像表头也不像条目 → 不敢数
    return n


def density_mark(after_cues, n, label):
    """密度只标 ⚠、不判 ✗——文档原话是「超出要解释」，工具不该比判据更严。

    `n` 为 0 时直接给 ✓：0 条不落在区间里，标 ⚠ 是拿噪音冒充发现（纯格式层那一批，
    审稿改动本来就是 0）。
    """
    if n == 0:
        return ('✓', f'{label} 0 条')
    dens = (n / len(after_cues) * 100) if after_cues else 0.0
    return ('✓' if DENSITY_LO <= dens <= DENSITY_HI else '⚠',
            f'{label} {n} 条 / 成品 {len(after_cues)} 条 = {dens:.2f} 条／百条'
            f'（参考 {DENSITY_LO}–{DENSITY_HI}，超出要能解释，不是错）')


def check(before, after, rows, ins, stage=None, findings=0):
    """算出全部标记，返回 ([(级别, 说明)], 退出码)。不打印——自测要能直接调它。"""
    bl, bc, kind = lv.load(before)
    al, ac, _ = lv.load(after)
    marks = []

    # ---- 结构与改动：全部交给 verify，判据只有一份 ----
    # 但「总行数不变」是只换文本时的判据。整体验收里，文件层（头部字段、样式表增删）
    # 本来就会改行数，所以这里判的是**对白行**——多一条少一条对白才是真错；
    # 非对白行的变化单独报出来让人看见，不判错。
    dlg_b, dlg_a = dialogue_count(bl), dialogue_count(al)
    delta_other = (len(al) - len(bl)) - len(ins)
    issues, st = lv.verify(before, after, rows, ins, allow_lines=delta_other)
    want_cues = len(bc) + len(ins)

    marks.append(('✓' if len(ac) == want_cues else '✗',
                  f'条数 {len(bc)} → {len(ac)}（应为 原 {len(bc)} + 补 {len(ins)} = {want_cues}）'))
    if kind == 'ass':
        want_dlg = dlg_b + len(ins)
        marks.append(('✓' if dlg_a == want_dlg else '✗',
                      f'对白行 {dlg_b} → {dlg_a}（应为 原 {dlg_b} + 补 {len(ins)} = {want_dlg}）'))
        if delta_other:
            marks.append(('✓' if abs(delta_other) <= 30 else '⚠',
                          f'非对白行 {len(bl) - dlg_b} → {len(al) - dlg_a}'
                          f'（{delta_other:+d} 行，样式表 {style_count(bl)} → {style_count(al)} 条）'))
    marks.append(('✓' if lv.has_bom(before) == lv.has_bom(after) else '✗',
                  f'BOM {"在" if lv.has_bom(after) else "无"}（改前{"在" if lv.has_bom(before) else "无"}）'))

    if rows or ins:
        marks.append(('✓' if st['不符'] == 0 and st['多改'] == 0 and not issues else '✗',
                      f'改动 声明 {st["声明"]} / 在位 {st["在位"]} / 不符 {st["不符"]} / 多改 {st["多改"]}'))
        if ins:
            marks.append(('✓' if st['补行'] == len(ins) else '✗',
                          f'补行 应补 {len(ins)} / 到位 {st["补行"]}'))
    for msg in issues:
        marks.append(('✗', msg))

    # ---- 补行的位置：只查补行，不查全文件行序（理由见 inserts_position_ok） ----
    pos_bad = False
    if ins:
        bad_pos = inserts_position_ok(ac, ins)
        pos_bad = bool(bad_pos)
        marks.append(('✓' if not bad_pos else '✗',
                      f'补行位置 {len(ins)} 条都夹在邻居之间' if not bad_pos else
                      f'补行位置有 {len(bad_pos)} 条不对：' + '；'.join(bad_pos[:3])))

    # ---- 三数对账：findings → 并表 → 落地（`GQ-24`） ----
    # verify 只认改动表，它证明不了「该改的没漏」——漏在表外的条目它不知道存在。
    # 起点往上提一层：给了 findings 就把「找出来几条」和「并进表几条」摆在一起看。
    # 去重会让并表数合法地小于 findings 数，所以只有「并表比 findings 还多」才判 ✗。
    if findings:
        merged = len(rows)
        if merged > findings:
            marks.append(('✗', f'改动表 {merged} 条 > findings {findings} 条——表里多出来的条目没有出处'))
        elif merged < findings:
            marks.append(('⚠', f'findings {findings} 条 → 改动表 {merged} 条，少 {findings - merged} 条：'
                                f'去重要说得出来是哪几条（这个机器判不了）'))
        else:
            marks.append(('✓', f'findings {findings} 条 → 改动表 {merged} 条，一条没丢'))

    # ---- 落盘密度：审稿改动与格式层动作分开报（`GQ-28`） ----
    # 合成一个数时没有判读价值：某集只因同集做了话轮标记就报 18.59 条／百条，
    # 看着像审稿失控，其实那 78 条是格式。有 `stage` 列就分开，没有就都算审稿改动。
    fmt_n = sum(1 for r in rows if (r.get('stage') or '').lower() == 'fmt')
    marks.append(density_mark(ac, len(rows) - fmt_n + len(ins), '审稿改动'))
    if fmt_n:
        marks.append(density_mark(ac, fmt_n, '格式层动作'))

    # ---- 人判痕迹：只查在不在 ----
    st_ok, st_msg = stage_trace(stage)
    marks.append(('⚠' if st_ok is None else ('✓' if st_ok else '✗'), st_msg))

    code = 1 if any(l == '✗' for l, _ in marks) else 0
    if pos_bad:
        # 补行挪了位，条号序列会整体错位，verify 那堆「未声明被改」全是它的连带——
        # 不说这一句，人会去翻几百条，而真正要修的只有一条位置。
        marks.append(('!', '先修补行位置：条号序列已经错位，上面「未声明被改」那些多半是它的连带'))
    return marks, code


def main():
    ap = argparse.ArgumentParser(description='验收：一条命令报出该对的全部数字')
    ap.add_argument('before', help='改前字幕（结构体检时给同一份）')
    ap.add_argument('after', nargs='?', help='改后字幕；不传就和 before 同一份')
    ap.add_argument('--changes', help='改动表 TSV（no / old / new）')
    ap.add_argument('--inserts', help='补行表 TSV（只支持 ASS）')
    ap.add_argument('--stage', help='定档.md，只查痕迹不判内容')
    ap.add_argument('--findings', action='append', metavar='TSV',
                    help='findings 表，可多次给（两遍各一份）。给了就做三数对账')
    a = ap.parse_args()

    before = Path(a.before)
    after = Path(a.after) if a.after else before
    rows = lv.read_changes(Path(a.changes)) if a.changes else []
    ins = lv.read_inserts(Path(a.inserts)) if a.inserts else []
    find_n = 0
    for p in a.findings or []:
        n = count_findings(p)
        if n is None:
            print(f'  ⚠  {p} 数不出条数（有一行既不像表头也不像条目），本轮不做三数对账',
                  file=sys.stderr)
        else:
            find_n += n

    try:
        bl, bc, kind = lv.load(before)
        al, ac, _ = lv.load(after)
    except SystemExit:
        raise
    except Exception as e:
        print(f'读文件出错：{e}', file=sys.stderr)
        return 2

    marks, code = check(before, after, rows, ins, a.stage, find_n)
    print(f'[验收] {after.name}（{lv.load(after)[2]}，改前 {before.name}）')
    for lvl, msg in marks:
        print(f'  {lvl}  {msg}')

    bad = sum(1 for m, _ in marks if m == '✗')
    warn = sum(1 for m, _ in marks if m == '⚠')
    total = sum(1 for m, _ in marks if m != '!')
    print(f'\n{total} 项：{total - bad - warn} 过 / {bad} 不过 / {warn} 要看一眼')
    if bad:
        print('有 ✗ 的项目，这集没验完——按上面的提示修，别绕过。')
        return 1
    if warn:
        print('没有 ✗；⚠ 的几项自己确认一下（数字异常不等于错，但要说得出为什么）。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
