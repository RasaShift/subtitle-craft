# 最近修改：未记录 2026-10-31（2026-11-01 补署名；此前无此行，原改动内容无据可查）
# -*- coding: utf-8 -*-
# 作者：DeepSeek-V4.1-Flash 2026-10-31
# 用途：给「通读复核」环节搭隔离目录——把某一片的通读模型结果，转成可逐条判的候选清单，
#       连同两档判据、交接卡片、回填模板一起拷到项目外，只给那一个路径。
#       与 isolate_blind.py 的区别：那个给的是「题面」（让模型自己跑一遍），
#       这个给的是「别人跑出来的候选」（让复核方判该不该改），两者不能混用。
# 用法：
#   python prefilter_semantic.py --ep-tag <集标签>                    # 先预筛
#   python isolate_review.py --ep-tag <集标签> --result <原样 result.json> \
#          --sheet 02_工作区/pilot/<集>/worksheet_02.tsv \
#          --only 02_工作区/<季>/semantic/<预筛表>.tsv           [--chunk 2] [--force]
#   （--only 是推荐用法：直接吃预筛表，一次跑完「预筛 → 打包」；别手工造过滤后的 result.json，
#     那份中间产物不留档、不可复现——实测重跑一次就退回全量候选。）
#   --prefiltered 单独用时可只改卡片措辞（不筛），一般不需要手敲。
# 产物（默认 E:\评测隔离\<日期>\<集号>\review-<片>\）：
#   candidates.md      主料：每条候选一段，带行号/中英/模型说法/上下文（复核方只读这个）
#   candidates.tsv     同一批数据的机器版，用于回填后对账
#   p4_rubric.md       两档判据（只拷评分表，不拷提示词——复核方不重跑通读）
#   terminology.json    译名/职衔口径（全剧，不含答案）——判专名类候选要用
#   character_tone.json 人物声音判据（主线剧集资产，续篇一般没有）——判「像不像这个人说的」要用
#   review_template.tsv 回填骨架
#   交接卡片.md         贴给新会话的整段话
# 闭环三段：本脚本产候选 → 复核会话判两档交回 review.tsv → `check_review.py` 机器对账（exit 1 打回）
#          → `split_by_tier.py` 分流（应改自动落 / 不改抽 1/3 人判）。
# 注意：回填表 review.tsv 实测会带 UTF-8 BOM（模板没有），对账读表一律 utf-8-sig（10-31 演练实测）。
# 注意：本脚本**不拷任何标准答案**；result.json 里的 model 字段也不写进清单，
#       免得复核方按「哪个模型报的」下判断。
import argparse
import datetime
import json
import pathlib
import shutil
import sys

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from isolate_blind import copy_refs  # noqa: E402  # 口径参考件拷贝只有一份实现

# 根目录跟随当前工作目录（在哪个剧库里跑，就读那个剧库）。
ROOT = pathlib.Path.cwd()
CTX = 2  # 上下文行数：前后各 2 行，与 golden 盲测题面口径一致


def load_sheet(path):
    """读取分片表：行号 / 时间码 / 中文 / 英文。返回 {ln: dict} 与有序行号表。"""
    rows = path.read_text(encoding='utf-8-sig').strip().split('\n')
    header = rows[0].split('\t')
    assert header[:4] == ['行号', '时间码', '中文', '英文'], f'分片表列不对：{header[:4]}'
    out, order = {}, []
    for r in rows[1:]:
        c = r.split('\t')
        if len(c) < 4:
            continue
        ln = int(c[0])
        out[ln] = {'tc': c[1], 'cn': c[2], 'en': c[3]}
        order.append(ln)
    return out, order


def load_only(path):
    """读预筛产物（prefilter_semantic 的 `<key>_新增.tsv`）第 1 列，返回行号白名单。

    为什么要接这个参数（2026-10-31）：预筛剔掉的行不该进复核清单——让复核方
    去看「成品里已经改好的」等于白烧他的额度。旧做法是手工造一份过滤后的
    result.json 再传进来，那份中间产物不留档、不可复现；现在直接把预筛产出的
    表当白名单，一次跑完「预筛 → 打包」，链路上不出现手搓文件。
    """
    lines = path.read_text(encoding='utf-8-sig').strip().split('\n')
    assert lines[0].split('\t')[0] == 'ln', f'不是预筛新增表（首列应为 ln）：{path}'
    return [int(r.split('\t')[0]) for r in lines[1:] if r.strip()]


def clean(s):
    return (s or '').replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ep-tag', required=True, help='集标签，随便起，只用来分目录')
    ap.add_argument('--result', required=True, help='通读模型交回的 result.json')
    ap.add_argument('--sheet', required=True, help='该片分片表 worksheet_0N.tsv')
    ap.add_argument('--chunk', default='', help='片号；缺省从 sheet 文件名取')
    ap.add_argument('--sub', default='', help='子目录名；缺省 review-<片>')
    ap.add_argument('--base', required=True,
                    help='隔离根目录，必须在项目外。必填不给默认值——跑批产物落进库里等于污染真身')
    ap.add_argument('--date', default='', help='目录日期，缺省今天')
    ap.add_argument('--no-tone', action='store_true',
                    help='不拷人物声音卡（NB/R 两线用）')
    ap.add_argument('--prefiltered', action='store_true',
                    help='候选已经过 prefilter_semantic 预筛（只含真新增），卡片里注明。'
                         '一般不用手敲——给了 --only 会自动开')
    ap.add_argument('--only', default='',
                    help='预筛新增表（prefilter 的 `<key>_新增.tsv`）；只把表内行号打进清单，'
                         '其余丢弃。给了它就自动开 --prefiltered 说明')
    ap.add_argument('--force', action='store_true', help='目标已存在时覆盖')
    args = ap.parse_args()

    res_p = pathlib.Path(args.result)
    sheet_p = pathlib.Path(args.sheet)
    for p in (res_p, sheet_p):
        if not p.exists():
            raise SystemExit(f'文件不存在：{p}')

    chunk = args.chunk or (sheet_p.stem.rsplit('_', 1)[-1]
                           if sheet_p.stem.startswith('worksheet') else 'x')
    sub = args.sub or f'review-{chunk}'

    base = pathlib.Path(args.base).resolve()
    try:
        base.relative_to(ROOT)
        raise SystemExit(f'隔离目录 {base} 在项目内，起不到隔离作用，请换到项目外')
    except ValueError:
        pass

    day = args.date or datetime.date.today().strftime('%Y%m%d')
    out = base / day / args.ep_tag / sub
    if out.exists() and not args.force:
        raise SystemExit(f'{out} 已存在（可能上次跑过）。要重建请加 --force')
    out.mkdir(parents=True, exist_ok=True)

    sheet, order = load_sheet(sheet_p)
    lo, hi = order[0], order[-1]
    payload = json.loads(res_p.read_text(encoding='utf-8'))
    issues = payload.get('issues', [])
    assert isinstance(issues, list) and issues, 'result.json 里没有 issues'

    # 行号必须落在本片范围内，否则说明 result 和 sheet 不是同一片
    bad = [i['ln'] for i in issues if i['ln'] not in sheet]
    if bad:
        raise SystemExit(f'{len(bad)} 条 issue 的行号不在本片（{lo}–{hi}）：{bad[:8]}…'
                         f'——检查 result 与 sheet 是不是同一片')

    issues.sort(key=lambda x: x['ln'])
    pos = {ln: k for k, ln in enumerate(order)}
    raw_n = len(issues)

    if args.only:
        keep = load_only(pathlib.Path(args.only))
        dropped = [i['ln'] for i in issues if i['ln'] not in keep]
        issues = [i for i in issues if i['ln'] in keep]
        if not issues:
            raise SystemExit(f'--only 表里与本片候选没有交集：{args.only}')
        args.prefiltered = True
        print(f'  预筛白名单 {len(keep)} 行，剔除 {len(dropped)} 条候选'
              + (f'（{dropped[:8]}…）' if dropped else ''))
    if args.prefiltered and raw_n == len(issues):
        raw_n = 0        # 没真剔掉东西时别提「原始 N 条」，免得写出「31 条里的 31 条」

    md = [f'# 通读候选清单 · {args.ep_tag} 第 {chunk} 片', '',
          f'本片 {len(order)} 行（{lo}–{hi}），模型提出 **{len(issues)} 条**候选，'
          f'逐条给出判定。', '']
    if args.prefiltered:
        md += ['> 本清单**已经过预筛**：原始结果里的候选，凡成品已解决的都已剔除'
               '（旧 finding 改的就是同一处、或旧轮次只改英文轨而模型同样只提英文轨）。'
               '剩下的都是**真新增**——即成品里还没解决的。逐条按 C1/C2/C3 判，'
               '**看得懂就判「不改」**，不必因为它「读着不够漂亮」放行。', '']
    md += ['> 编号（01、02…）只是为了好引用，**真正的行号是每条的「行号」**。'
           '回填表里必须抄那个行号，不要用编号代替。', '']
    tsv = ['行号\t时间码\t中文\t英文\t模型类型\t模型严重度\t模型置信\t模型证据\t模型建议\t上下文']
    tpl = ['行号\t初判\t理由\t建议改法']

    for k, it in enumerate(issues, 1):
        ln = it['ln']
        row = sheet[ln]
        i = pos[ln]
        prev = order[max(0, i - CTX):i]
        nxt = order[i + 1:i + 1 + CTX]
        ctx_bits = []
        for x in prev:
            ctx_bits.append(f'L{x} 「{clean(sheet[x]["cn"])}」')
        for x in nxt:
            ctx_bits.append(f'L{x} 「{clean(sheet[x]["cn"])}」')
        ctx = ' ／ '.join(ctx_bits)

        md += [f'## {k:02d}　行号 {ln}　{row["tc"]}', '',
               f'- 中文：{clean(row["cn"])}',
               f'- 英文：{clean(row["en"])}',
               f'- 模型说（{it.get("severity", "")}／{it.get("problem_type", "")}，'
               f'置信 {it.get("confidence", "")}）：{clean(it.get("evidence", ""))}']
        if it.get('suggest'):
            md.append(f'- 模型建议改成：{clean(it["suggest"])}')
        md += [f'- 上下文：{ctx}', '']

        tsv.append('\t'.join([
            str(ln), row['tc'], clean(row['cn']), clean(row['en']),
            clean(it.get('problem_type', '')), clean(it.get('severity', '')),
            str(it.get('confidence', '')), clean(it.get('evidence', '')),
            clean(it.get('suggest', '')), ctx]))
        tpl.append(f'{ln}\t\t\t')

    (out / 'candidates.md').write_text('\n'.join(md) + '\n', encoding='utf-8', newline='\n')
    (out / 'candidates.tsv').write_text('\n'.join(tsv) + '\n', encoding='utf-8', newline='\n')
    (out / 'review_template.tsv').write_text('\n'.join(tpl) + '\n', encoding='utf-8', newline='\n')
    shutil.copy2(ROOT / '04_工具' / 'prompts' / 'p4_rubric.md', out / 'p4_rubric.md')
    refs = copy_refs(out, tone=not args.no_tone)
    PURPOSE = {'terminology.json': '译名/职衔口径——判专名类候选时以它为准',
               'character_tone.json': '人物声音判据——判「像不像这个人说的」时看它'}
    ref_lines = '\n'.join(
        f'| `{n}` | 口径参考件（全剧统一，**不含本片答案**）：{PURPOSE.get(n, "")} |'
        for n in refs)

    prefilter_note = ''
    if args.prefiltered:
        lead = (f'模型原始报出 {raw_n} 条，其中成品里**已经改好的**已剔除，剩下'
                if raw_n else '成品里**已经改好的**已剔除，剩下')
        prefilter_note = f"""
> **这批候选已经过预筛**：{lead} {len(issues)} 条是**真新增**（成品里还没解决的）。
> 所以这里每一条都是「成品还没解决」的——逐条按 C1/C2/C3 判，**别按「看着挺顺就判不改」的惯性刷**，
> 也别因为它「读着别扭」就往应改放（应改会自动落盘，没人再看第二眼）。
"""

    card = f"""# 交接卡片：通读复核（{args.ep_tag} 第 {chunk} 片）

你只需要读这个目录（`{out}`），**不要读项目里的任何文件、不要去找标准答案**。
{prefilter_note}
## 材料（都在本目录）

| 文件 | 用途 |
|---|---|
| `candidates.md` | **主料**：{len(issues)} 条候选，每条带行号、中英原文、模型的说法、上下文 |
| `p4_rubric.md` | 判据：看「一、两档怎么分」那一节，只问一句「观众会看错吗」 |
| `review_template.tsv` | 回填骨架，行号已填好，别增删行、别改行号 |
{ref_lines}

> **参考件怎么用**：判专名类候选（人名/职衔/车型之类的译法）时以 `terminology.json` 为准；判「这句话像不像这个人说的」时看 `character_tone.json`。两份都是全剧口径、**不含本片答案**，也没有任何「哪一行该改」的信息（其中 `character_tone.json` 的 `vo_ranges_*` 是**别集**标注，与本片无关）。

## 任务

对 `candidates.md` 里**每一条**候选判一档（**只有两档**）：

- **应改** —— 观众会看错：理解反了 / 看不懂 / 剧情链断了（C1、C2、C3，见 `p4_rubric.md` 第二节）
- **不改** —— 三条都不命中。**包括**读着别扭、翻译腔、不如原文精确、人物声音不够贴

判的是**观众会不会看错剧情**，不是「中文写得漂不漂亮」。

## 规矩

1. 行号原样抄 `candidates.md` 里的「行号」，不许用编号（01、02…）代替，也不许重新编号。
2. 拿不准就问一句「观众会看错吗」——看不出错就是「不改」。
   **不要为了保险往「应改」放**：应改会自动落盘，没有第二个人再看它一眼。
3. 每条都要给一条理由（≤40 字），说清是「哪两个字翻错了」或「为什么没问题」，不要写「表达不准确」这种空话。
4. 觉得模型给的建议改法更好就填进「建议改法」；你自己有更好的写法也可以填；不需要改就留空。
5. 只有这两个值，不许自创档位（**「可改可不改」已作废**，也不要「建议改」「部分可改」这类）。

## 交回

1. `review.tsv` —— 填好的回填表（制表符分隔，编码 UTF-8）
2. `简报.md` —— 两档各多少条 / 你认为最拿不准的 3 条（写行号 + 为什么两头都能讲）/ 你对这批候选整体信噪比的判断
3. 如果你加载了任何项目内的技能或文档，在简报里列出来（便于判断有没有污染）
"""
    (out / '交接卡片.md').write_text(card, encoding='utf-8', newline='\n')

    print(f'已建隔离目录：{out}')
    print(f'  candidates.md    {len(issues)} 条候选（本片 {len(order)} 行，{lo}–{hi}）')
    print(f'  candidates.tsv / review_template.tsv / p4_rubric.md / '
          f'口径参考件（{"、".join(refs)}）/ 交接卡片.md')
    print()
    print('把下面这句贴给新会话：')
    print(f'  读 {out}\\交接卡片.md，按里面的要求做完，把 review.tsv 和 简报.md 写回同一目录。')


if __name__ == '__main__':
    main()
