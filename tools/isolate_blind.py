# 最近修改：未记录 2026-10-31（2026-11-01 补署名；此前无此行，原改动内容无据可查）
# 作者：DeepSeek 2026-10-25
"""盲测隔离：把题面拷到项目外，只给那一个路径。

为什么需要脚本而不是靠叮嘱：
  2026-10-25 E03 那轮，跑盲测的会话读了项目工作日志里我写的答案细节，15 条应改里
  5 条被提示。日志是项目内公共文件，它能读——靠"不许读别的文件"约束不住，
  只有把题面搬到项目外、只给它一个路径，它才真的没有别的可读。

用法：
  # 1) 建隔离目录（在项目外），拷题面 + 提示词，打印可直接粘贴的交接话术
  python isolate_blind.py --ep-tag <集标签>
  # 2) 跑完把结果 json 放回项目，再算分
  python eval_golden.py --result <结果.json> --name <模型名> --ep-tag <集标签>

产物（默认 E:\\评测隔离\\<日期>\\<集号>\\）：
  题面 TSV              唯一该给它的文件（golden_50.blind.tsv 或 worksheet_0N.tsv）
  p4_semantic.md        提示词
  p4_rubric.md          评分口径
  terminology.json      译名/职衔口径（全剧，不含答案）
  character_tone.json   人物声音判据（主线剧集的资产，续篇一般没有，用 --no-tone 跳过）
  交接卡片.md            贴给新会话的整段话
  result_template.json  输出骨架（让它照着填，省得格式跑偏）
"""
import argparse
import datetime
import json
import pathlib
import shutil
import sys

sys.stdout.reconfigure(encoding='utf-8')

# 根目录跟随当前工作目录（在哪个剧库里跑，就读那个剧库的术语表），不跟脚本位置走。
ROOT = pathlib.Path.cwd()


def copy_refs(out, tone=True):
    """拷「口径参考件」到隔离目录并回读校验；缺文件或坏 JSON 直接报错退出。

    为什么必须拷：`p4_semantic.md` 第 5 行把 terminology.json 与 character_tone.json
    列为输入，第 4 节又规定「以 terminology.json 为准；与表不一致才算问题」。
    不拷＝模型手上没有译名口径、也没有人物声音判据，L3 语域 / L4 人物声音两层失去依据。
    历史：2026-10-25 那两批隔离目录里就没有（当时的脚本只拷提示词与题面）。

    `tone=False` 用于**没有人物声音卡的那条剧线**（人物声音卡是主线剧集的资产，续篇往往没有）。
    **用显式参数而不是按 ep_tag 猜剧线**——ep_tag 是自由文本，给续篇传一个主线风格的季号时，
    按正则判断必然猜错（2026-10-31 改）。

    注意：这两个文件是**全剧口径**、不含本片答案；character_tone.json 里可能带
    若干别的集号标注，与本片无关，卡片里已注明。

    **这两个文件属于术语表类，不入本库**（红线：术语表永不出门）。本函数只负责
    「从剧库某处拷到隔离目录」，路径默认按剧库约定，也可由调用方覆盖。
    """
    got = []
    refs = ROOT / '02_工作区' / 'global' / 'terminology.json'
    if not refs.exists():
        raise SystemExit(f'术语表不存在：{refs}')
    shutil.copy2(refs, out / refs.name)
    try:
        json.loads((out / refs.name).read_text(encoding='utf-8'))
    except Exception as e:
        raise SystemExit(f'术语表拷过去后无法解析：{e}')
    got.append(refs.name)

    if tone:
        card = ROOT / '02_工作区' / 'pilot' / 'character_tone.json'
        if not card.exists():
            raise SystemExit(f'人物声音卡不存在：{card}')
        shutil.copy2(card, out / card.name)
        try:
            json.loads((out / card.name).read_text(encoding='utf-8'))
        except Exception as e:
            raise SystemExit(f'人物声音卡拷过去后无法解析：{e}')
        got.append(card.name)
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ep-tag', required=True, help='集标签，随便起，只用来分目录')
    ap.add_argument('--blind', default='', help='题面路径；缺省取 pilot/<集>/golden_50.blind.tsv')
    ap.add_argument('--sheet', default='',
                    help='改用整集分片当题面（如 worksheet_01.tsv），用于真实集数的双向验证')
    ap.add_argument('--base', required=True,
                    help='隔离根目录，必须在项目外。必填不给默认值——跑批产物落进库里等于污染真身')
    ap.add_argument('--date', default='', help='目录日期，缺省今天')
    ap.add_argument('--sub', default='', help='集号下的子目录，如 b2（同集多批并行时用）')
    ap.add_argument('--no-tone', action='store_true',
                    help='不拷人物声音卡（续篇一般没有这个资产）')
    ap.add_argument('--force', action='store_true', help='目标已存在时覆盖')
    args = ap.parse_args()

    blind = pathlib.Path(args.sheet) if args.sheet else (
        pathlib.Path(args.blind) if args.blind else
        ROOT / '02_工作区' / 'pilot' / args.ep_tag / 'golden_50.blind.tsv')
    if not blind.is_absolute():
        blind = blind.resolve()
    if not blind.exists():
        raise SystemExit(f'题面不存在：{blind}')

    base = pathlib.Path(args.base).resolve()
    # 硬护栏：隔离目录绝不能落在项目里，否则等于没隔离
    try:
        base.relative_to(ROOT)
        raise SystemExit(f'隔离目录 {base} 在项目内，起不到隔离作用，请换到项目外')
    except ValueError:
        pass

    day = args.date or datetime.date.today().strftime('%Y%m%d')
    out = base / day / args.ep_tag / args.sub if args.sub else base / day / args.ep_tag
    if out.exists() and not args.force:
        raise SystemExit(f'{out} 已存在（可能上次跑过）。确认要重建请加 --force')
    out.mkdir(parents=True, exist_ok=True)

    rows = blind.read_text(encoding='utf-8-sig').strip().split('\n')
    header, body = rows[0].split('\t'), rows[1:]
    n = len(body)

    prompt_dir = ROOT / '04_工具' / 'prompts'      # 各剧库自己的提示词目录
    for name in ('p4_semantic.md', 'p4_rubric.md'):
        shutil.copy2(prompt_dir / name, out / name)
    sheet_name = blind.name
    shutil.copy2(blind, out / sheet_name)
    refs = copy_refs(out, tone=not args.no_tone)
    ref_lines = '\n'.join(
        f'- `{n}`　口径参考件（全剧统一，**不含本片答案**）' for n in refs)

    chunk_label = (sheet_name.rsplit('_', 1)[-1].rsplit('.', 1)[0]
                   if sheet_name.startswith('worksheet') else 'golden_50')
    (out / 'result_template.json').write_text(json.dumps({
        'model': '（填你自己的模型名；不知道就写 unknown，不要猜）',
        'chunk': chunk_label,
        'vo_ranges': [[22, 40], [51, 66]],
        'issues': [{'ln': 0, 'dimension': 'L0', 'problem_type': 'C1.2 主客颠倒',
                    'severity': 'major', 'confidence': 0.9,
                    'cn': '中文原文', 'en': '英文原文',
                    'evidence': '为什么要改（说清观众会在哪一点上理解错）',
                    'suggest': '改成什么'}],
    }, ensure_ascii=False, indent=1), encoding='utf-8')

    card = f"""# 交接卡片：{args.ep_tag} 语义审读（盲测）

你只需要读这个目录，不要读项目里的其他文件。

## 材料（都在本目录）
- `p4_semantic.md`　提示词（按它执行）
- `p4_rubric.md`　　判定标准与档位
- `{sheet_name}`　题面：{n} 句，{len(header)} 列
  列序：{' / '.join(header)}
{ref_lines}
- `result_template.json`　输出骨架

> **口径参考件怎么用**：`terminology.json` 是译名/职衔的既定口径，与表不一致就是 C2.3 术语错位（见 `p4_semantic.md`）；`character_tone.json` 是人物声音参考，**2026-10-31 起只在「观众会认错说话人」时才用**（C2.2），单纯「不够像这个人」不报。两份都**不含本片答案**，也不含任何「哪一行该改」的信息；其中 `vo_ranges_*` 这类字段是**别集**的标注，与本片无关，别当线索用。

## 要你做的事
按 `p4_semantic.md` 的规则，对题面里**每一句**判断是否要改。题面是本片第 {chunk_label} 片的全量 {n} 行，
前后文都在同一个文件里，判某一行时请自己上下各看 2 行（提示词第「审读方法」第 1 条）。

## 输出（两种任选，A 为首选）

**A. 问题清单** —— 写 `result.json` 到**本目录**，结构照 `result_template.json`：

- `issues`：只放你认为该改的条目，`ln` 用题面第一列的行号（原样照抄）。
  每条必须给全 `ln` / `dimension` / `problem_type` / `severity` / `confidence` / `evidence` 六个字段，
  `suggest` 可不填（留空字符串）；字段缺一项这条就算不合格（脚本会丢弃）。
- `vo_ranges`：内心独白的**连续区间**，如 `[[22,40],[51,66]]`；**标单点等于没做**（要求成段）。
- **只报 C1/C2/C3**（观众会理解反／看不懂／剧情链断）。这三条内拿不准就报，标 minor；
  三条都不命中，一条都不报——「译得不够好、欠准、读着别扭、翻译腔」**不报**。
  `issues` 为空是正常结果，不要为了凑数往回找。

**B. 整篇译文** —— 若你更习惯直接重译，则写 `translations.json`：
`{{"model": "…", "translations": {{"212": "译文…", "213": "译文…"}}}}`
**只放你改动过的行**，没改的不要列。行号照抄题面第一列。

## 硬性要求

1. **文件必须写在上面这个目录里**。直接写对话里不算交付（会被回收站清掉）。
   已在别处生成过就拷过来，并回读一次确认 `json.loads` 能解析。
2. **`\\N` 必须原样保留**：源文有几处 `\\N`，你的译文就要有几处（它是字幕换行符）。
3. **不要在译文里加解释**（不要写「（原译有误）」这类批注），只给能直接替换的成品。
4. 一次读完 190 行如果吃力，可以分两三次写文件，但**最后合成一个 JSON**。
5. `model` 字段写你自己真实的模型名；不确定就写 `unknown`，不要猜。

## 交付
把本目录的完整路径 + 文件名告诉我。**不要**额外解释判定过程。
"""
    (out / '交接卡片.md').write_text(card, encoding='utf-8')

    print(f'隔离目录已建：{out}')
    print(f'  题面 {n} 句（{len(header)} 列）')
    print(f'  提示词 + 评分标准 + 输出骨架 + 口径参考件（{"、".join(refs)}）已复制')
    print()
    print('给新会话的话（整段复制）：')
    print('-' * 60)
    print(f'读 {out} 目录下的 p4_semantic.md、p4_rubric.md，')
    print(f'按 p4_semantic.md 的规则审 {sheet_name} 里的 {n} 句，')
    print(f'结果写成 JSON 存到 {out / "result.json"}（结构照 result_template.json）。')
    print(f'只读这个目录，不要读项目里的其他文件。')
    print('-' * 60)


if __name__ == '__main__':
    main()
