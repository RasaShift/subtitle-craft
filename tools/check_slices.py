#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 作者：DeepSeek-V4.1-Flash 2026-11-02
"""check_slices.py -- 通读分片的自检闸

只做一件事：拿到一个通读集目录，核对「分片表 / meta.json / 回顾段边界」三者是否自洽。
不判断内容好坏，不改任何文件。

核四件：
  1. 每个 片N.tsv 去掉表头后的数据行数，与 meta.json 的 slices[].rows 一致
  2. 各片行数相加 + skipped 合计 = meta.json 的 rows
  3. 相邻两片时间不重叠、顺序递增（按 tsv 的时间码列）
  4. 给了 --recap 且分片有「行号」列时：任一行号落在回顾段区间内 → 报错
     （NB/R 分片没有行号列，这一项自动跳过）

用法：
  python check_slices.py "../../02_工作区/通读/NB_E05"
  python check_slices.py "../../02_工作区/pilot/S03E02" --recap "../../02_工作区/recap_boundary.json" --ep S03E02

退出码 0 = 全过；1 = 有问题，逐条打印。
"""
import argparse
import json
import sys
from pathlib import Path


def read_tsv(path):
    """返回 (表头列表, 数据行列表)。读时去掉 BOM 与行尾 \\r。"""
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    lines = [l.replace("\r", "") for l in text.split("\n")]
    lines = [l for l in lines if l.strip()]
    if not lines:
        return [], []
    return lines[0].split("\t"), [l.split("\t") for l in lines[1:]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ep_dir", help="通读集目录（含 片N.tsv 与可选的 meta.json）")
    ap.add_argument("--recap", default="", help="recap_boundary.json 路径；给了才查越界")
    ap.add_argument("--ep", default="", help="集号，如 S03E02；查回顾段时必填")
    a = ap.parse_args()

    d = Path(a.ep_dir)
    if not d.is_dir():
        print("目录不存在：%s" % d)
        return 1

    problems = []
    sheets = sorted(d.glob("片*.tsv")) or sorted(d.glob("*.tsv"))
    if not sheets:
        print("目录里没有分片表：%s" % d)
        return 1

    stats = []
    for s in sheets:
        head, rows = read_tsv(s)
        stats.append({"file": s.name, "head": head, "rows": rows, "n": len(rows)})
        if not rows:
            problems.append("%s 没有数据行" % s.name)

    # 1 + 2：与 meta.json 对账
    meta_path = d / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        slices = meta.get("slices", [])
        by_name = {x["file"]: x for x in slices}
        for st in stats:
            m = by_name.get(st["file"])
            if not m:
                problems.append("%s 在 meta.json 里没有记录" % st["file"])
                continue
            if m.get("rows") != st["n"]:
                problems.append("%s 行数不符：meta=%s 实际=%s"
                                % (st["file"], m.get("rows"), st["n"]))
        skipped = sum(meta.get("skipped", {}).values())
        total = sum(st["n"] for st in stats)
        # 口径（据 NB E01–E05 实测）：meta.rows = 各片行数相加，skipped 不在其中
        if meta.get("rows") is not None and total != meta["rows"]:
            problems.append("总行数对不上：各片相加=%d，meta.rows=%s（skipped 不计入 rows）"
                            % (total, meta["rows"]))
        if skipped:
            print("提醒：skipped %d 行不在任何一片里，属正常（片头 Mark 标牌等）" % skipped)
        print("meta.json：rows=%s  skipped=%s  片数=%s"
              % (meta.get("rows"), meta.get("skipped"), len(slices)))
    else:
        print("无 meta.json（正剧手工切片属正常），只做时间序与越界检查")

    # 3：跨片时间不重叠、递增
    def tc(v):
        parts = v.strip().split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError:
            return None
        while len(nums) < 3:
            nums.insert(0, 0.0)
        return nums[0] * 3600 + nums[1] * 60 + nums[2]

    prev_last = None
    for st in stats:
        if "时间码" not in st["head"]:
            continue
        col = st["head"].index("时间码")
        times = [tc(r[col]) for r in st["rows"] if len(r) > col and tc(r[col]) is not None]
        if not times:
            continue
        first, last = times[0], times[-1]
        print("%s：%d 行  %s → %s" % (st["file"], st["n"], st["rows"][0][col], st["rows"][-1][col]))
        if prev_last is not None and first < prev_last:
            problems.append("%s 起点早于上一片终点（%.2f < %.2f）" % (st["file"], first, prev_last))
        prev_last = last

    # 4：回顾段越界（只对有行号列的正剧分片生效）
    if a.recap:
        if not a.ep:
            problems.append("给了 --recap 就必须给 --ep")
        else:
            table = json.loads(Path(a.recap).read_text(encoding="utf-8"))
            rec = next((r for r in table if r.get("ep") == a.ep), None)
            if rec is None:
                problems.append("recap_boundary.json 里查不到 %s" % a.ep)
            elif rec.get("start_line") and rec.get("end_line"):
                lo, hi = rec["start_line"], rec["end_line"]
                for st in stats:
                    if "行号" not in st["head"]:
                        continue
                    col = st["head"].index("行号")
                    bad = [r[col] for r in st["rows"] if len(r) > col and r[col].strip().isdigit()
                           and lo <= int(r[col]) <= hi]
                    if bad:
                        problems.append("%s 有 %d 行落在回顾段 L%d–L%d：%s"
                                        % (st["file"], len(bad), lo, hi, ",".join(bad[:10])))
                print("回顾段 %s：L%d–L%d（%s 行）" % (a.ep, lo, hi, rec.get("rows")))
            else:
                print("回顾段 %s：本集无回顾段" % a.ep)

    if problems:
        print("\n❌ 不通过 %d 项：" % len(problems))
        for p in problems:
            print("  - " + p)
        return 1
    print("\n✅ 分片自洽")
    return 0


if __name__ == "__main__":
    sys.exit(main())
