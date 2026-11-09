#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# 最近修改：2026-11-08T2130（VERSION 带时分；--docs 生效时三处硬编码一并改到、归档豁免认 `_存档`、老库不再为本库文档报噪音）
"""doc_lint.py -- 文档体检：把「文档该怎么写」从陈述句改成可机检的断言

为什么有它：文档乱掉的共因只有三条——规矩写在被管的文件里、超限没有出口、
违约零成本（文件长了不报错）。前两条靠 `docs/文档落点表.md` 治，这一条靠本脚本治。

用法：
    python tools/doc_lint.py          # 全查，退出码 1 = 有 ERROR
    python tools/doc_lint.py -q       # 只报 ERROR
    python tools/doc_lint.py --docs 06_治理   # 老库：文档目录不叫 docs 时用它指过去

查五类：
  1. 行数超限       —— 每份文档的上限写在 LIMITS 里，和落点表一一对应
  2. 待办区留残行   —— 「已完成 / ✅」出现在待办区就是没删干净
  3. 判定记录体例   —— 收结论的，出现表格 / 非标准二级节 / 单条超 8 行
  4. 单行过长       —— 多半是表格被对齐 pad 过
  5. 引用死链       —— 写进文档的 `xx/yy.md` 实际不存在

只报不管：不做自动修复。要不要拆、拆到哪，人决定。超限了用 `doc_split.py`。

上限从哪来：每个数字都对应一次事故或一条已验证的体量，见 `docs/文档落点表.md`。
改数字之前先想清楚为什么原来那个不够。

**版本（2026-11-08 加）**：本脚本是「拷进别的库」分发的，副本过期没有任何征兆——
剧库那份跑得动、报「没有 ERROR」，只是少了一批检查。所以输出末尾带 `vYYYY-MM-DDTHHMM`，
两边一对就知道是不是同一版。**改本脚本必须改 VERSION 这一行。**
**只到天不够（2026-11-08T21:30 改）**：本脚本一天内改过四次，天级版本号分不出前后，
`2026-11-08` 那一版和三小时后的版本打印出来一模一样。时分是这条检查的最低分辨率。
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

VERSION = '2026-11-09T1517'

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'

# 与 docs/文档落点表.md 一一对应，改一处必须改另一处
LIMITS = {
    # 2026-11-07 由 120 降到 60：台账已拆成「简表 + 详情」，简表只放一行一条。
    # 超 60 是提醒「该往 待办台账-详情.md 搬」，不是叫你删条目——上限的用途变了。
    '待办台账.md':   {'lines': 60,  'entry': 0},
    # 账本不设总量上限，所以条目里**没有** `lines` 键（见判定记录 11-06「判定记录是账本」）。
    # 曾经写死 350，和落点表的「总量不设限」、env_check 的 400 三方打架——同一个数写三处必漂。
    '判定记录.md':   {'entry': 8, 'no_table': True, 'vol': 150},
    '文档落点表.md': {'lines': 80,  'entry': 0},
    '做法.md':       {'lines': 60,  'entry': 0},
    '接棒必读.md':   {'lines': 60,  'entry': 0},
    '流程总图.md':   {'lines': 100, 'entry': 0},   # 清单型：步骤与接口，长了是步骤多了
    '命名准则.md':   {'lines': 60,  'entry': 0},   # 规则型：只放骨架，例子在各自文档
    # 术语表按稳定型管：只收「被用错过的词」，不该无限长。100 的出处见落点表——
    # 现有 4 条实测 37 行，留一倍余量给下一个出事的词。满了删词，不拆表。
    'GLOSSARY.md':   {'lines': 100, 'entry': 0},
    # 2026-11-08 由 200 降到 135：长度**不跟 GQ 条数走，跟病根数走**——那天 18 条
    # GQ 只有 2 个病根，按条写 100 行，按病根 25 行就够。判词正文进
    # `docs/archive/答复_YYYYMM.md`，前台只留「病根 + 判法 + 登记表」；广播同理。
    # 超 135 是提醒「该归档了」，不是文档写长了。
    '协作协议.md':  {'lines': 135, 'entry': 0},
}
GONGXU_LIMIT = 130      # 工序主干（工序-<名>.md）：判据型，超了说明判据在堆，该拆成两道工序
GONGXU_LIST_LIMIT = 200 # 工序分片（工序-<名>-<后缀>.md）：清单型，一条一行，长的是必要信息
                        # 200 是观察值不是判据——现在三份分别是 83 / 52 / 48，到顶再议分卷，别删条目
HANDBOOK_LIMIT = 800    # 工序手册（工序-<名>-手册.md）：装「怎么干」——步骤、命令、模板、自查、交回。
                        # 它不是判据（判据在 工序-<名>.md，上限 130），是操作细节的合集，压短就是删知识。
                        # 审稿那份 679 行，是 2026-11-07 技能重做时由原 3 份技能手册归组而来。
                        # 到 800 先想按「开工准备 / 判读 / 品控落盘」拆成三卷，别删条目。
README_LIMIT = 110      # 2026-11-05 由 80 上调（工具表每加一件 +1 行，80 逼得说明压成黑话）；
                        # 2026-11-08 由 100 调到 110：第 29 件工具上表，101 行当场报红。
                        # 这条只给工具表涨——别的节超了照旧压，别拿它当整体放宽。
LONG_LINE = 900         # 任何一行超过这个字符数，基本是被 pad 出来的表格
SKILL_LIMIT = 250       # skills/*/SKILL.md：技能正文直接吃每轮加载的 token，超了先想拆层

# 技能行数豁免：经确认「拆了更糟」的，登记在这里。值 = (豁免基线行数, 理由+日期+谁定的)。
# 豁免不是不再管，是换了个基线——涨过基线照样报 ERROR。
# 登记前必须先答「拆的代价是什么」，答不出来就退回 250。
# 2026-11-07：唯一一条（read-qc 302 行）随 #18 薄壳化撤掉——压到 54 行，
# 理由行当初就写着「重写压缩完应落到 250 以内，届时本行删掉」。当前无豁免。
SKILL_EXEMPT = {}
DONE_WORDS = re.compile(r'已完成|✅|☑')
SEC_OK = re.compile(r'^## (?:\S、|过程去哪了|\d{4}-\d{2}-\d{2})')
MD_REF = re.compile(r'`([^`\n]+?\.md)`')


def read(p: Path):
    return p.read_text(encoding='utf-8-sig').split('\n')


def sections(lines):
    """按 # / ## / ### 切块，产出 (标题, 起始行号, 行列表)。"""
    cur, buf = None, []
    for i, ln in enumerate(lines, 1):
        if ln.startswith('#'):
            if cur:
                yield cur[0], cur[1], buf
            cur, buf = (ln.strip(), i), []
        else:
            buf.append(ln)
    if cur:
        yield cur[0], cur[1], buf


def check_lines(path: Path, limit: int, hint: str) -> list:
    n = len(read(path)) - 1
    if n > limit:
        return [('ERROR', f'{path.name} 共 {n} 行，超上限 {limit}——{hint}')]
    return []


def check_skills(root: Path = None) -> list:
    """技能正文的行数。

    不塞进 walk_md：那套检查（章节规范、禁用词、「已完成」占位）是按 docs/ 的形态写的，
    套到技能上会刷一片误报。技能只查一条最要紧的——行数，它直接吃每轮加载的 token。

    豁免走 SKILL_EXEMPT：登记过的按自己的基线放行（并留一行 WARN 说明不是漏报），
    涨过基线照样报 ERROR——否则「豁免」会变成「永久免检」。
    """
    out = []
    for p in sorted(((root or ROOT) / 'skills').glob('*/SKILL.md')):
        name = p.parent.name
        try:
            n = len(read(p)) - 1
        except Exception as e:
            out.append(('WARN', f'skills/{name}/SKILL.md 读不动：{e}'))
            continue
        cap, why = SKILL_EXEMPT.get(name, (SKILL_LIMIT, ''))
        if n > cap:
            hint = ('——先想拆层（通用 / 私人），不是压缩条目' if not why else
                    f'——已登记豁免基线 {cap}，又涨 {n - cap} 行；豁免不是不再管')
            out.append(('ERROR', f'skills/{name}/SKILL.md 共 {n} 行，超上限 {cap}{hint}'))
        elif why and n > SKILL_LIMIT:
            out.append(('WARN', f'skills/{name}/SKILL.md 共 {n} 行，超通用上限 {SKILL_LIMIT}，'
                                f'按豁免放行：{why}'))
    return out


def check_taizhang_note(path: Path = None) -> list:
    """台账详情：每个 `## #N` 段必须有一行「核于」。

    条目的「卡在哪」会过期。2026-11-07 一天里三条写错：#19 数字过期（写 20 处、
    实际 15 处）、#20 顺序过期（写「跟 #18 一起做」、#18 早做完）、#17 指的是两个
    根本不在本库的脚本。共同点：没有一个记着自己哪天被核过。

    只查「有没有这一行」，不查日期新旧：旧不等于错（有的条目本来就稳定），
    机器也判不了「这条还成不成立」——那是人的事，机器只负责让「没核过」藏不住。
    """
    p = path or (DOCS / '待办台账-详情.md')
    if not p.is_file():
        return []
    out = []
    for title, lineno, buf in sections(read(p)):
        if not re.match(r'^## #\d+', title):
            continue
        if not any('核于' in ln for ln in buf):
            out.append(('WARN', f'{p.name} L{lineno}「{title[3:][:26]}」没有「核于」一行'
                                 f'——没记哪天核过，等于没人核过'))
    return out


def check_skill_scripts() -> list:
    """技能里提到的 `xxx.py`，要么本库真有，要么在「脚本 vs 本库」表里交代过下落。

    起因：8 份技能是从第一个剧库复制进来的，搬了技能没搬工具，手册里 22 个脚本名
    指向 `日常杂活/04_工具/`。但**不能要求它们都在本库**——剧专用的（名字带集号、
    含真实人名表）搬进来就违反红线，正确做法是「不搬，但说清在哪、为什么」。

    所以判据不是「脚本存在」，是「有交代」：正文里提到、对照表里列了，就算过。
    这样以后新加一个没交代的脚本名，照样会被抓出来。
    """
    have = {q.name for q in (ROOT / 'tools').glob('*.py')}
    have |= {q.name for q in (ROOT / 'skills').rglob('*.py')}
    pat = re.compile(r'\b([a-z_][a-z0-9_]*\.py)\b')
    out = []
    for d in sorted((ROOT / 'skills').glob('subtitle-*')):
        files = [d / 'SKILL.md'] + sorted((d / 'references').glob('*.md'))
        said, declared = set(), set()
        for f in files:
            if not f.is_file():
                continue
            try:
                txt = f.read_text(encoding='utf-8')
            except Exception:
                continue
            said |= set(pat.findall(txt))
            m = re.search(r'## 本手册用到的脚本 vs 本库\n(.*?)(?=\n## |\Z)', txt, re.S)
            if m:
                declared |= set(pat.findall(m.group(1)))
        miss = said - have - declared
        if miss:
            out.append(('WARN', f'skills/{d.name} 提到 {len(miss)} 个本库没有、也没交代下落的脚本：'
                                 f'{"、".join(sorted(miss)[:5])}'
                                 f'——在手册开头加「脚本 vs 本库」表说明它去哪了'))
    return out


def check_body(path: Path, cfg) -> list:
    out = []
    lines = read(path)
    if cfg.get('no_table'):
        tb = [i for i, ln in enumerate(lines, 1) if ln.lstrip().startswith('|')]
        if tb:
            out.append(('WARN', f'{path.name} 出现 {len(tb)} 行表格（首个在 L{tb[0]}）'
                                f'——判定记录只收结论，表格一般是过程'))
    for title, lineno, buf in sections(lines):
        if (path.name == '判定记录.md' and title.startswith('##')
                and not title.startswith('###') and not SEC_OK.match(title)):
            out.append(('WARN', f'{path.name} L{lineno} 非标准二级节「{title[3:][:30]}」'
                                f'——日期段/临时段要拆成 ### 条目'))
        if not title.startswith('###') or not cfg.get('entry'):
            continue
        n = sum(1 for ln in buf if ln.strip())
        if n > cfg['entry']:
            out.append(('WARN', f'{path.name} L{lineno} 单条 {n} 行（上限 {cfg["entry"]}）：'
                                f'{title[4:][:40]}'))
    # 判定记录是账本：只增不减，套「总量上限」只会在某天逼人硬砍记忆。
    # 所以控的不是「一共存多少」，是「一口气读多少」——当前卷满 150 行整卷搬进 archive，索引留指针。
    if path.name == '判定记录.md' and cfg.get('vol'):
        vs = [i for i, ln in enumerate(lines) if re.match(r'^## \d{4}-\d{2}-\d{2}', ln)]
        if not vs:
            out.append(('ERROR', f'{path.name} 找不到日期卷——当前卷要用 `## YYYY-MM-DD` 起节'))
        else:
            # 量「第一个日期卷到文末」= 主文件里实际压着的全部正文。
            # 原来量的是 vs[-1]（最后一个日期段），旧卷赖在文件里照样过检：
            # 11-07 当天主文件 275 行，被量的只有 62 行，「当前卷 150」形同虚设。
            n = len(lines) - vs[0]
            if n > cfg['vol']:
                out.append(('ERROR', f'{path.name} 当前卷 {n} 行，超 {cfg["vol"]}——'
                                     f'整卷搬进 docs/archive/判定记录_YYYYMM.md，索引里留一行指针'))
    return out


def check_taizhang(path: Path) -> list:
    """待办区（`## 一、` 到文末）不许留「已完成 / ✅」。

    只查列表行和表格行（`-` / `*` / `|` 开头）——说明段、档位标题不算待办行；
    一刀切会把头部「做完的去 archive」那句也判死，然后人就开始加 --no-verify。

    2026-11-07 改：原来只扫「`## 一、` → `## 二、`」之间。台账拆成四档后，
    这个区间只盖住第一档，后三档等于没查。现在一路扫到文末；
    `## 一、` 之前的说明区仍是安全区，头部可以正常写「已完成搬去哪」。
    """
    out = []
    lines = read(path)
    lo = None
    for i, ln in enumerate(lines):
        if ln.startswith('## 一、'):
            lo = i
            break
    if lo is None:
        # 找不到就报，不静默通过——静默失效比漏检更贵
        return [('ERROR', f'{path.name} 找不到待办区——第一档要用 `## 一、` 起节')]
    for k, ln in enumerate(lines[lo:], lo + 1):
        if not re.match(r'^\s*(?:[-*+]\s|\|)', ln):
            continue                      # 说明段、正文段落不算待办行
        if DONE_WORDS.search(ln):
            out.append(('ERROR', f'{path.name} L{k} 待办区留了已完成行（做完要整行删）：'
                                 f'{ln[:40]}'))
    return out


def _git(*args):
    """跑一条 git，成功返回 stdout（strip 过），失败返回 None。

    没装 git、不在仓库里、文件没提交过——一律 None，让调用方退回「不查」。
    不许因为环境问题报 ERROR：新库或解压出来的副本没有 `.git` 是正常情况。
    """
    try:
        r = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)
        return (r.stdout or '').strip() if r.returncode == 0 else None
    except (FileNotFoundError, OSError):
        return None


def git_last_commit(rel: str):
    """这个文件最后一次提交的 hash。没 git / 没提交过就返回 None。"""
    return _git('log', '-1', '--format=%H', '--', rel) or None


def is_ancestor(a: str, b: str) -> bool:
    """a 的提交是不是在 b 之前（a 是 b 的祖先）。"""
    try:
        r = subprocess.run(['git', 'merge-base', '--is-ancestor', a, b],
                           cwd=ROOT, capture_output=True, text=True)
        return r.returncode == 0
    except (FileNotFoundError, OSError):
        return False


UPDATED = re.compile(r'最后更新[：:]\s*(\d{4}-\d{2}-\d{2})')
# 这几份一变，接棒必读就该跟着变——它们承载的是「现在到哪了」和「为什么」
# 详情也算：改了某条的卡点/前置，往往意味着「现在到哪」变了，接棒必读该同步。
# 只跟简表会漏——活都干在详情里，简表反而是最后才动的那份。
# 2026-11-08T21:30（剧库 #9）：**不许写死 `docs/`，也不许在模块级算**。
# 原来是 `docs/…` 硬编码，而 --docs 只改 `globals()['DOCS']`，发生在模块加载之后——
# 在模块级拼的话拿到的还是旧 DOCS。所以这三行必须在函数里现算（见 check_handoff_doc）。
FOLLOWER_NAMES = ['待办台账.md', '待办台账-详情.md', '判定记录.md']


def check_handoff_doc() -> list:
    """接棒必读：必须有「最后更新」，且不能落后于台账/判定记录。

    固定文件名换来「永远只有一份」，代价是它不会自己过期、只会安静地落后——
    读者看到固定名字，默认它是最新的。这条检查就是补那个代价。

    **按 commit 比，不按日期比**（2026-11-07 改）。原版用 `--format=%cs` 只拿到
    日期，同一天改多少次它都报不出来：11-07 18:02–18:12 之间台账与判定记录改了
    **6 次**、接棒必读一次没跟，检查跑了两遍都报「通过」，而接棒必读里「实证仍待
    重建」那行当时已经是假的。**同一天内的落后是常态，不是边界情况。**
    """
    p = DOCS / '接棒必读.md'
    if not p.is_file():
        return [('ERROR', f'缺 {DOCS.name}/接棒必读.md——交接没有入口，新会话只能靠猜')]
    followers = [f'{DOCS.name}/{n}' for n in FOLLOWER_NAMES]
    out = []
    m = None
    for ln in read(p):
        m = UPDATED.search(ln)
        if m:
            break
    if not m:
        out.append(('WARN', '接棒必读.md 没有「最后更新：YYYY-MM-DD」行——'
                            '固定文件名下，读者没法判断它是不是旧的'))
        return out

    # ① 改了还没提交：接棒必读多半也要跟，提交前一起看一眼
    # 先刷一次索引：刚 commit 完立刻跑，stat 缓存还没更新，会把已提交的文件报成「有未提交」。
    # 光 refresh 不够——`--refresh` 返回后第一把 status 仍可能读到旧缓存（2026-11-08 第三次撞上）。
    # 所以判「连续两次都脏」才报：真改了文件，两次都会脏；只是缓存没跟上，第二次就干净了。
    # 宁可漏报一次缓存边界，也不要给一条假 WARN——假报一次，这条检查就没人看了。
    _git('update-index', '-q', '--refresh')
    dirty = _git('status', '--porcelain', '--', *followers)
    if dirty and _git('status', '--porcelain', '--', *followers):
        out.append(('WARN', '台账 / 判定记录有未提交的改动——提交前确认接棒必读跟上了'
                            '（同一件事一次提交，别分两笔）'))

    # ② 已提交的：比 commit，不比日期
    mine = git_last_commit(f'{DOCS.name}/接棒必读.md')
    if not mine:                      # 新库 / 没初始化 git：有日期行就够了
        return out
    for rel in followers:
        theirs = git_last_commit(rel)
        if not theirs or theirs == mine:
            continue                  # 同一次提交改的，正是要的状态
        if not is_ancestor(theirs, mine):
            out.append(('WARN', f'{rel.split("/")[-1]} 的最近一次提交比接棒必读新'
                                f'——若这次改动影响「现在到哪」，记得同步'))
    return out


def walk_md():
    """查引用时该扫哪些文件：docs/ 全部、README、以及各技能的 SKILL.md。

    技能正文现在只当目录，全靠指针——指针不查，等于这个目录可以乱指。
    2026-11-07 补：此前只扫 docs/ 与 README，技能里指向 `references/` 的
    `docs/` 路径一条都没验过；薄壳化后技能正文全是指针，这个盲区必须堵上。
    """
    paths = sorted(DOCS.rglob('*.md')) + [ROOT / 'README.md']
    paths += sorted((ROOT / 'skills').glob('*/SKILL.md'))
    for p in paths:
        if p.is_file():
            yield p


def check_long_lines() -> list:
    out = []
    for p in walk_md():
        for i, ln in enumerate(read(p), 1):
            if len(ln) > LONG_LINE:
                out.append(('WARN', f'{p.name} L{i} 单行 {len(ln)} 字符（上限 {LONG_LINE}）'
                                    f'——多半是表格被对齐 pad 过'))
    return out


def check_refs() -> list:
    """文档里写的 `a/b.md` 真不真。

    两条豁免，都是被误报逼出来的：
      · 只查带 `/` 的路径。纯文件名太含糊（常是「待生成的产物」或隔壁库的文档），
        查了全是误报；误报会让整条检查被无视。
      · 首段是隔壁库目录（`ROOT.parent / 首段` 存在）就跳过——跨库指针本来就找不到。

    第三条（2026-11-07）：归档目录整个跳过。归档是历史账本，记的是当时的事实
    与当时的路径；结构一改里面全是「死链」，但那些链在写下那一刻是真的。查它等于逼人
    篡改历史，而账本只增不改。
    **2026-11-08T21:30（剧库 #9）**：原来只认英文 `archive`，老库把归档叫 `_存档/`，
    于是理由完全成立、检查照样不豁免——固定报 3 条死链。名字不是判据，**凡是归档目录都豁免**。
    """
    out = []
    for p in walk_md():
        if any(x in p.parts for x in ('archive', '_存档', '归档')):
            continue
        for i, ln in enumerate(read(p), 1):
            for ref in MD_REF.findall(ln):
                ref = ref.strip()
                if '/' not in ref:
                    continue
                if ' ' in ref or ':' in ref:
                    continue
                if re.search(r'xxx|YYYY|XX|<\w+>|\*', ref, re.I):
                    continue
                first = ref.split('/')[0]
                if first and (ROOT.parent / first).is_dir() and first != ROOT.name:
                    continue                      # 指向隔壁库，本库管不着
                # 2026-11-08 加（GQ-12）：老库（文档目录不叫 docs）里，`docs/` `tools/`
                # `examples/` `skills/` 开头指的是**通用库的四层**，本库根下当然没有。
                # 判据用「DOCS 不叫 docs」定语境，**不能看见这四个词就跳**——本库 DOCS
                # 就叫 docs，那样我自己写错路径也查不出来，等于把闸拆了换安静。
                if DOCS.name != 'docs' and first in ('docs', 'tools', 'examples', 'skills'):
                    continue
                if (ROOT / ref).is_file() or (p.parent / ref).is_file():
                    continue
                out.append(('WARN', f'{p.relative_to(ROOT).as_posix()} L{i} '
                                     f'引用的文件不存在：{ref}'))
    return out



MEMORY_LIMIT = 3000     # 自动注入层的字符上限（2026-11-08）
LOG_LIMIT = 20000       # 单日日志字节上限（2026-11-08）


def check_memory(root: Path = None) -> list:
    """记忆层两道闸（2026-11-08 加）。

    为什么要（原话）：MEMORY.md 是唯一「新会话不用读、系统自动塞进上下文」的一层，
    它一超长，注入时会被压缩，靠后的条目直接看不到——**比没写更糟，因为你以为写了**。
    日志同理：一天写过 20K 就不是日志，是聊天记录转录，没人会读完，等于没记。

    这两项原先一个字的上限都没有（docs/ 那套每份都有行数闸），所以必然越写越长。
    """
    out = []
    mem = (root or ROOT) / '.workbuddy' / 'memory'
    m = mem / 'MEMORY.md'
    if m.is_file():
        n = len(m.read_text(encoding='utf-8', errors='replace'))
        if n > MEMORY_LIMIT:
            out.append(('WARN', f'MEMORY.md {n} 字符（上限 {MEMORY_LIMIT}）：'
                                f'它是自动注入层，超了会被压缩、靠后条目看不到。'
                                f'只留「不看就会干错事」的，其余搬 docs/'))
    logs = sorted(mem.glob('20[0-9][0-9]-[0-9][0-9]-[0-9][0-9].md'))
    if logs:
        last = logs[-1]
        n = last.stat().st_size
        if n > LOG_LIMIT:
            out.append(('WARN', f'日志 {last.name} {n / 1024:.0f}K（上限 {LOG_LIMIT / 1024:.0f}K）：'
                                f'这不是日志是聊天记录。只留判断过程和为什么改主意，'
                                f'操作流水不记'))
    return out



ARCHIVE_LIMIT = 200     # 归档每份上限（2026-11-08）


def check_archive(root: Path = None) -> list:
    """归档目录也要有上限（2026-11-08 加）。

    原先进了 archive 就"安全"了：落点表写着每份 ≤200，但没人查——`已完成_202611.md`
    长到 219 行、`doc_lint` 一直绿。**归档是只增型，没有闸就会一直长**，
    而它长的正是最不该读的那部分（旧内容）。

    例外：判定记录卷不设限——它是账本，拆卷不拆内容（落点表原话）。
    """
    out = []
    d = (root or ROOT) / 'docs' / 'archive'
    if not d.is_dir():
        return out
    for p in sorted(d.glob('*.md')):
        if p.name.startswith('判定记录_'):
            continue
        n = len(p.read_text(encoding='utf-8', errors='replace').splitlines())
        if n > ARCHIVE_LIMIT:
            out.append(('ERROR', f'archive/{p.name} {n} 行，超 {ARCHIVE_LIMIT}——'
                                 f'归档只增型，满了开新卷（`-2` / `-3`），别往里硬塞'))
    return out


# 2026-11-08（#38）加：哪四件脚本要项目目录才跑得动。这是「能独立跑 N 件」的真身——
# 文档报的数由 total 减这四件算出来；名单过期就报 ERROR，不许文档和代码各说一套。
NEEDS_PROJECT = ('blind_sample.py', 'isolate_review.py',
                 'prefilter_semantic.py', 'validate_semantic.py',
                 'gen_progress.py')

# 只认这几种明确写法。宁可漏认，也别宽到把「12 件审稿辅助」当总数——
# 误报一次，这道闸就会被人绕开，等于没设。
_CNT_PATTERNS = (
    (re.compile(r'(?:一共\s*)?(\d{1,3})\s*(?:件|个)(?:工具|脚本)'), 'total'),
    (re.compile(r'工具\s*(\d{1,3})\s*件'), 'total'),
    (re.compile(r'能独立跑的?\s*(\d{1,3})\s*件'), 'runnable'),
    (re.compile(r'(\d{1,3})\s*件能独立跑'), 'runnable'),
    # 表格报表写法：`| 通用工具 | **30 件**：… |`（加粗 + 紧跟冒号）。
    # 2026-11-09 加（`#46`）：上面四条都要求「N 件」后面跟「工具 / 脚本」，
    # 表格里隔着 ` | **` 一条也匹配不上——`接棒必读:17` 那处从没被核过。
    # **代价**：文档里如果「引用」这个写法（例：表格里写 **30 件**：），也会被
    # 当成本库总数报出来。写引用别加粗，或换个说法。
    (re.compile(r'\*\*(\d{1,3})\s*件\*\*\s*[：:]'), 'total'),
)


# 术语表词条区禁用的状态词。这些词写的是「现在到哪」，而术语表只回答
# 「这个词指什么」——两者混在一起，抄的那一刻对，之后没人回来改。
STATE_WORDS = ('下一批', '目前', '现在', '当前', '还剩', '暂时', '最近')


def check_glossary_state() -> list:
    """术语表词条区不许出现状态词。

    2026-11-09 加（自己犯的）：从协议 §5 抄了一句「下一批从 `#12` 起」，
    抄的时候是对的，抄完立刻过期——台账已经排到 `#41`。**术语表里给一条错的
    规则，比不给更糟**：人是来查「这词到底什么意思」的，不会去核对号段。
    同源病根是 `GQ-17`「转述丢条件」——转述的副本不知道原件已经变了。

    只查词条区（`## 词条` 到 `## Flagged ambiguities` 之间）：头部要写
    「现有几条」这类体量说明，歧义节要写「曾经指两个东西」这类历史。
    """
    p = DOCS / 'GLOSSARY.md'
    if not p.is_file():
        return []
    lines = read(p)
    try:
        a = next(i for i, l in enumerate(lines) if l.startswith('## 词条'))
        b = next(i for i, l in enumerate(lines) if l.startswith('## Flagged'))
    except StopIteration:
        return []
    out = []
    for i in range(a, b):
        for w in STATE_WORDS:
            if w in lines[i]:
                out.append(('WARN', f'{DOCS.name}/GLOSSARY.md L{i + 1} 出现状态词「{w}」'
                                     f'——这里只回答词是什么意思；号段、进度这类会过期的'
                                     f'状态去真身看'))
                break
    return out


def check_progress_head() -> list:
    """进度台账头部必须自带「禁止手改」。

    2026-11-08 加（剧库 #8）：GQ-2 只说了「骨架缺这份文件」，没给生成方式，于是
    各库自己建、自己填——**给空文件的结果就是各家手填，而手填必然落后，落后还不报错**。
    骨架已换成 `gen_progress.py` 的产物，这条闸防的是「下一个人把它改回手工表」。
    没有这份文件的库不报：通用库自己就用剧库的台账。
    """
    p = DOCS / '进度台账.md'
    if not p.is_file():
        return []
    head = '\n'.join(read(p)[:6])
    if '禁止手改' in head or '禁手改' in head:
        return []
    return [('WARN', f'{DOCS.name}/进度台账.md 头部没写「禁止手改」——'
                     f'这份应由 tools/gen_progress.py 生成；手填必然落后，且不会报错')]


def check_counts() -> list:
    """文档里写死的「N 件工具 / N 件能独立跑」必须跟 tools/ 实际文件数一致。

    为什么不立一张「参数真身表」：表本身会过期，等于又造一份真身——GQ-15 否掉
    `变更记录.tsv` 用的就是这条理由。这里的真身是磁盘上的文件，文档只许跟它对。

    只查两样零成本数得出来的（工具总数、可独立跑件数）。工时 / 密度区间 / 画布
    数不出唯一值，不在此列，靠判据首行版本戳加广播管。
    **CHANGELOG 不查**：它记的是各版本当时的事实，旧版本写旧数是对的。
    """
    out = []
    tdir = ROOT / 'tools'
    if not tdir.is_dir():
        # 老库语境（文档目录不叫 `docs`）：工具在 `04_工具/`，本来就没有 `tools/`——
        # 这不是缺件，是这库的布局不同。报出来就是给老库制造假警，与 GQ-12 同一个病根：
        # 剧库刚把 WARN 从 23 压到 7，多一条它又多一条要解释的噪音。
        if DOCS.name != 'docs':
            return []
        return [('WARN', '找不到 tools/ 目录，跳过件数核对')]
    have = {p.name for p in tdir.glob('*.py')}
    total = len(have)
    present = [n for n in NEEDS_PROJECT if n in have]
    # 名单核验只在「本库就是通用库本身」时才有意义——`scaffold.py` 只在通用库的
    # `tools/` 里，骨架库只带 4 件工程件，名单当然不齐：那不是过期，是没装。
    # 2026-11-08 改：原来用「装了其中任意一件」当语境判定，骨架带上 gen_progress 之后
    # 这个前提就破了，新库一建出来就报一条假 ERROR。**判语境要看只有本库才有的东西。**
    if (ROOT / 'tools' / 'scaffold.py').is_file() and len(present) != len(NEEDS_PROJECT):
        lost = [n for n in NEEDS_PROJECT if n not in have]
        out.append(('ERROR', f'doc_lint 的 NEEDS_PROJECT 里有 {lost}，tools/ 下找不到——'
                             f'名单过期或工具改名了，先改 tools/doc_lint.py 里这个常量'))
    if not present:
        return out
    runnable = total - len(present)

    targets = list(sorted(DOCS.glob('*.md')))
    rp = ROOT / 'README.md'
    if rp.is_file():
        targets.append(rp)
    for p in targets:
        text = p.read_text(encoding='utf-8', errors='replace')
        for pat, kind in _CNT_PATTERNS:
            for m in pat.finditer(text):
                g = [x for x in m.groups() if x]
                if not g:
                    continue
                n, want = int(g[0]), (total if kind == 'total' else runnable)
                if n != want:
                    label = '工具总数' if kind == 'total' else '能独立跑的件数'
                    out.append(('ERROR', f'{p.name} 写{label} {n}，实际 {want}'
                                         f'（tools/ 共 {total} 个脚本）：改文档，别改这个检查'))
    return out


# 库根下的「工具层」：不带编号，给命令敲。另一半是「数据层」：`NN_中文名`，给人翻。
# 两段的分法见 docs/命名准则.md 第六节，检查在 check_top_dirs。
TOP_DIR_OK = {'docs', 'tools', 'skills', 'examples'}


def check_top_dirs(root: Path = None) -> list:
    """库根下的每个目录，要么是 `NN_中文名`（给人翻），要么在工具层白名单里（给命令敲）。

    2026-11-09 加。为什么有这条：骨架里 `01_源字幕` 和 `docs/` 并存本来就是**两段**，
    不是混用；但边界没写成能判的话，每加一个目录都要重开一次讨论——`05_参考资料`
    该叫什么、该不该叫 04，就是这么争起来的。判据一句话：**这名字会不会出现在命令行里**。

    **不查编号连续性**：日常杂活 `03` 用了两次（`03_工作区` / `03_成品`），查了就是
    给别人的库报噪音。假 WARN 比漏报更伤——报一次没人看，闸就废了。
    也不查顺序号对不对：`05_参考资料` 中间空着 `04`（工具位），那是刻意的。
    """
    r = root or ROOT
    out = []
    if not r.is_dir():
        return out
    for d in sorted(r.iterdir()):
        if not d.is_dir() or d.name[0] in '._':
            continue                      # `.git` / `_tmp` 不是内容，不归这条管
        if d.name in TOP_DIR_OK:
            continue
        # 数字前缀 + 中文名：`01_源字幕`、`05_参考资料`。数字两位是排序需要，不是分类编号。
        if re.fullmatch(r'\d{2}_[^\x00-\x7f].*', d.name):
            continue
        out.append(('WARN', f'顶层目录 {d.name}/ 既不是「NN_中文名」（给人翻），也不在 '
                            f'{" / ".join(sorted(TOP_DIR_OK))} 里（给命令敲）——'
                            f'先定它属于哪段，名字照那段的规矩起（见 docs/命名准则.md 第六节）'))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('-q', '--quiet', action='store_true', help='只报 ERROR')
    ap.add_argument('--docs', metavar='DIR',
                    help='文档目录（默认 <库根>/docs）。老库的文档不叫 docs 时用它指过去：'
                         'ROOT 按脚本位置取，脚本放进 <库>/04_工具/ 之类就自动指库根，'
                         '但文档目录叫 docs 还是 06_治理，脚本猜不出来')
    a = ap.parse_args()

    # 2026-11-08 加。为什么不是 --root：ROOT 由脚本位置决定，装进老库的任意子目录都对，
    # 真正会错的是 DOCS 这一级——老库把文档放 `06_治理/`、不叫 `docs/`，于是每项都报
    # 「找不到 docs/X（跳过）」，全绿但一个字没查。--root 解决不了这个，只能 --docs。
    if a.docs:
        d = Path(a.docs).resolve()
        if not d.is_dir():
            print(f'[ERROR] --docs 指的目录不存在：{d}')
            return 2
        # 2026-11-08 加：ROOT 按脚本位置取，所以本脚本只能查自己所在的库。
        # 指到库外时原来会在 check_refs 里 `relative_to` 抛 ValueError 崩掉——
        # 跟 eol_guard 跨库静默全跳过是同一个病（GQ-12/GQ-18）：跨库用法必须有话说。
        try:
            d.relative_to(ROOT)
        except ValueError:
            print(f'[ERROR] --docs 指到本库之外了：{d}')
            print(f'本脚本按自身位置定库根（{ROOT}），只能查自己所在的库。')
            print('要查别的库，把 doc_lint.py 拷进那个库再跑——这也是副本会过期的原因，比对输出里的版本号。')
            return 2
        globals()['DOCS'] = d

    issues = []
    for name, cfg in LIMITS.items():
        p = DOCS / name
        if not p.is_file():
            # 2026-11-08T21:30（剧库 #9）：老库不该为「本库没有的文档」背 4 条噪音。
            # LIMITS 里「做法 / 流程总图 / 命名准则 / 协作协议」是通用库自己的，
            # 老库跑 --docs 时它们必然不在——照报就是逼人把 WARN 总数当背景音。
            # 判据用 DOCS 名字定语境，和 check_refs 同一条（不能看见文件名就跳，
            # 本库自己缺件必须照报 E R R O R 级的缺件提醒）。
            if DOCS.name != 'docs':
                continue
            issues.append(('WARN', f'找不到 {DOCS.name}/{name}（跳过）'))
            continue
        try:
            if cfg.get('lines'):      # 账本不设总量上限，条目里就没有这个键
                issues += check_lines(p, cfg['lines'], '细节压成四句或搬 docs/archive/'
                                      if name != '接棒必读.md' else
                                      '接棒必读只当目录：把内容搬去真身，这里换成指针')
            if name == '待办台账.md':
                issues += check_taizhang(p)
            issues += check_body(p, cfg)
        except Exception as e:            # 单个文件炸了不许连累其它
            issues.append(('WARN', f'{name} 检查出错：{e}'))

    for p in sorted(DOCS.glob('工序-*.md')):
        # 三类名字，三套上限：
        #   工序-清稿.md            主干，判据型，130
        #   工序-清稿-规则清单.md    分片，清单型，200
        #   工序-清稿-手册.md        手册，操作细节合集，800（它不是判据，压短就是删知识）
        stem = p.stem
        if stem.endswith('-手册'):
            issues += check_lines(
                p, HANDBOOK_LIMIT,
                '手册装的是操作细节，压短等于删知识——到顶按工序阶段拆卷，别删条目')
            continue
        is_list = len(stem.split('-')) > 2
        issues += check_lines(
            p,
            GONGXU_LIST_LIMIT if is_list else GONGXU_LIMIT,
            '分片是清单型：一条一行，长的是必要信息——到顶先想分卷，别删条目'
            if is_list else
            '工序主干一页一件事，超了拆成两道工序（拆出来的明细放 工序-<名>-<后缀>.md）')
    rp = ROOT / 'README.md'
    if rp.is_file():
        issues += check_lines(rp, README_LIMIT, 'README 只放第一屏：是什么、怎么用、去哪看')

    issues += check_long_lines()
    issues += check_refs()
    issues += check_counts()
    issues += check_glossary_state()
    issues += check_progress_head()
    issues += check_handoff_doc()
    issues += check_skills()
    issues += check_taizhang_note()
    issues += check_skill_scripts()
    issues += check_memory()
    issues += check_archive()
    issues += check_top_dirs()

    err = [x for x in issues if x[0] == 'ERROR']
    warn = [x for x in issues if x[0] == 'WARN']
    for tag, msg in (err if a.quiet else err + warn):
        print(f'{tag:5} {msg}')

    # 版本一定要打在最后一行：拷贝式分发下，两边对不上就说明有一边该重拷了（GQ-13）
    ver = f'doc_lint v{VERSION}'
    if err:
        print(f'\n{len(err)} 个 ERROR、{len(warn)} 个 WARN。ERROR 要当场处理。〔{ver}〕')
        return 1
    if warn:
        print(f'\n没有 ERROR；{len(warn)} 个 WARN（看一眼，确认是过程还是结论）。〔{ver}〕')
        return 0
    print(f'文档体检通过。〔{ver}〕')
    return 0


if __name__ == '__main__':
    sys.exit(main())
