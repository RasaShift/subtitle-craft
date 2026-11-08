#!/usr/bin/env python3
# 最近修改：2026-11-08（加「用得极窄」与「F 类为零」两条提示，只提示不判失败）
# -*- coding: utf-8 -*-
"""check_rules.py -- 审稿改动表的「判据编号闸」

为什么有它：2026-11-08 拿某一集做整链演练，读完整片 548 条只报出 11 条，
净落盘密度 2.0 条/百行，贴着区间下限。查下来根因是**没读 `工序-审稿-判据.md`
的十四条**就开判——只靠「通读，看着别扭就报」，抓到的全是 C2（看不懂）里
「中文念不通」那一小撮，C1（理解反）一条都没有。而 C1 才是审稿专治的那类
（字都对、话也通、意思偏了半格）。

换句话说：**「我读过判据了」这句话，嘴上说没用，得拿 rule 列来证明。**
本脚本就是那个证明——每条的 rule 必须落在判据表的 33 条里，且按组分布要合理。

只做五件事：
  1. `rule` 列每一格都能切出判据编号，且每个编号都在判据表里
  2. 按组报分布（C1 / C2 / C3 / N / F）
  3. C1 组为零时**判失败**——那是「只读了通不通」的症状
  4. `rule` 用得极窄（≥10 条却只用 ≤2 种编号）时提示——那是「只扫了某一维」的症状
  5. F 类为零时提示——形态清单可能少带了一条（不判失败，好底本真可能没有）

**第 4 条为什么比第 5 条硬**：F 类是一组「形态」（话轮标记、残句、问句成陈述等），
底本干净时本来就可能是 0，硬拒会冤好稿；而「十几条全挤在一两种编号上」几乎不可能是
巧合——那是把整集只按一个维度扫了一遍。**能从产出反推过程的信号，才配当闸；推不出来的，
只能提示。**

不管怎么修：本脚本**只验编号，不验判断对不对**。编号填得对、判断是错的，
它一声不响。判得对不对只能靠人复核（`工序-审稿.md` 第 10 节抽样复验）。

用法：
    python tools/check_rules.py <改动表.tsv>
    python tools/check_rules.py <改动表.tsv> --table docs/工序-审稿-判据表.tsv
    python tools/check_rules.py <改动表.tsv> --allow-empty-c1     # 只出分布，不因 C1=0 退出 1
    python tools/check_rules.py <改动表.tsv> -q                   # 只报错，不打印分布

退出码：0 = 过；1 = 有错（编号不在表里 / 缺失 / C1 为零）。
另有一种直接拒：整表都用 `T1`／`DR3` 这类编号时判「这是对稿表」，对稿判据在
`docs/工序-对稿-*.md`，不归本脚本管——认出来拒掉，比按审稿表瞎报错有用。
"""
import argparse
import csv
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
DEFAULT_TABLE = TOOLS.parent / "docs" / "工序-审稿-判据表.tsv"

# C1.4 / N03 / F7 —— 一条里写多个时用 + 、 、 空格 分隔都认
CODE_RE = re.compile(r"(?:C[123]\.\d|N\d{2}|F\d)")
GROUP_RE = re.compile(r"^(C1|C2|C3|N|F)")

# 对稿表（triage_diff 出的）用的是 T1/T2/T3/T?/DR0/DR3/DR5，不归审稿判据管
ALIGN_RE = re.compile(r"^(T\d|T\?|DR\d)")


def read_codes(table: Path) -> dict:
    """判据表 → {'C1.1': '理解反', ...}"""
    out = {}
    with table.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            code = (row.get("编号") or "").strip()
            grp = (row.get("分组") or "").strip()
            if code:
                out[code] = grp
    return out


def read_rows(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f, delimiter="\t")
        cols = {(c or "").strip(): c for c in (rd.fieldnames or [])}
        if "rule" not in cols:
            return None, [], f"表里没有 rule 列（现有列：{', '.join(c or '?' for c in rd.fieldnames or [])}）"
        rows = [(i + 2, r) for i, r in enumerate(rd)]
        return cols["rule"], rows, None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet", help="改动表 tsv（要有一个 rule 列）")
    ap.add_argument("--table", default=str(DEFAULT_TABLE), help="判据表 tsv（默认 docs/工序-审稿-判据表.tsv）")
    ap.add_argument("--allow-empty-c1", action="store_true", help="C1 组为零不判失败，只提示")
    ap.add_argument("-q", "--quiet", action="store_true", help="不打印分组分布")
    a = ap.parse_args()

    sheet, table = Path(a.sheet), Path(a.table)
    if not sheet.is_file():
        print(f"✗ 找不到改动表：{sheet}")
        return 1
    if not table.is_file():
        print(f"✗ 找不到判据表：{table}")
        print("  本库默认在 docs/工序-审稿-判据表.tsv；换机器先确认这个文件在。")
        return 1

    known = read_codes(table)
    if not known:
        print(f"✗ 判据表读不出编号：{table}")
        return 1

    col, rows, err = read_rows(sheet)
    if err:
        print(f"✗ {err}")
        return 1

    problems = []
    warns = []
    counts = {}
    codes_all = set()
    align_hits = 0
    total = 0

    for ln, r in rows:
        raw = (r.get(col) or "").strip()
        if not raw:
            problems.append(f"第 {ln} 行：rule 是空的")
            continue
        total += 1
        codes = CODE_RE.findall(raw)
        if not codes:
            if ALIGN_RE.match(raw):
                align_hits += 1
                continue
            problems.append(f"第 {ln} 行：rule「{raw}」切不出判据编号")
            continue
        for c in codes:
            if c not in known:
                problems.append(f"第 {ln} 行：编号 {c} 不在判据表里")
                continue
            g = GROUP_RE.match(c)
            key = g.group(1) if g else "其他"
            counts[key] = counts.get(key, 0) + 1
            codes_all.add(c)

    if align_hits and total and align_hits == total:
        print(f"✗ 这是对稿表（{align_hits} 条全用 T／DR 编号），不是审稿表。")
        print("  T1/T2/T3/T? 与 DR 系列的判据在 docs/工序-对稿-*.md，本脚本不验。")
        return 1

    if not a.quiet:
        print(f"改动表 {sheet.name}：{total} 条带 rule" + (f"，{len(problems)} 条有问题" if problems else ""))
        if counts:
            order = ["C1", "C2", "C3", "N", "F", "其他"]
            line = "  ".join(f"{k} {counts[k]}" for k in order if counts.get(k))
            print(f"分组：{line}")

    if counts.get("C1", 0) == 0 and total:
        msg = ("⚠ C1 组一条都没有。审稿专治「字都对、话也通、意思偏了半格」，"
               "那类全归 C1（否定／动作方向／因果条件比较／时体／数字金额／肯否两读）。"
               "C1 为零、只有 C2「念不通」，多半是没读十四条就开判——先回去读 "
               "docs/工序-审稿-判据.md 第一节。")
        if a.allow_empty_c1:
            print(msg)
        else:
            problems.append(msg)

    # 用得极窄：十几条全挤在一两种编号上，几乎不可能是巧合，是把整集只按一个维度扫了一遍
    if total >= 10 and len(codes_all) <= 2:
        warns.append("⚠ 只用到 %d 种判据（%s），%d 条全挤在这一两维上。"
                     "多半是只扫了某一类，回头核一下别的类到底有没有。"
                     % (len(codes_all), "、".join(sorted(codes_all)), total))
    # F 为零：好底本真可能没有，只提示不判失败
    if total and counts.get("F", 0) == 0:
        warns.append("提示：F 类（形态类：话轮标记个数、残句、问句成陈述等）为 0。"
                     "真没有就正常；先确认派活单带的是**七类**清单而不是六类——"
                     "清单里少一条，就没人看那一条。")

    for w in warns:
        print(w)
    for p in problems:
        print(f"✗ {p}")
    if problems:
        print(f"共 {len(problems)} 处，未通过。")
        return 1
    print("✓ 判据编号全部对得上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
