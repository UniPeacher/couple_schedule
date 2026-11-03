#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
special_days —— 纪念日 / 节日引擎
移植自 zhangzhiwei0905/our-little-planet 的 src/lib/special-days.ts + lunar.ts，
适配 sched-share（Python 标准库 / SQLite / 双账号日程共享）。

设计要点
  1. 零依赖：只用 Python 标准库。农历数据表内嵌（1900–2100，源自 solarlunar 3.1.0
     的 lunarInfo，由工具脚本从该包真实导出，非手抄）。
  2. 纯函数：不碰数据库、不读环境变量、不依赖服务器时区。
     接收 date 或 'YYYY-MM-DD'，返回可 JSON 序列化的 dict/list。
     服务端（喂 notifications / ntfy 推送）与前端可各自独立调用。
  3. 时区安全：内部一律 date 对象做日历运算，不出现「毫秒差 / 86400000」这类
     被夏令时/闰秒打破的算法（原 JS 版在跨时区部署时会算错天数，此处已规避）。

对原项目的移植与修正
  - DAY_MILESTONES（整百/520/1314/之后每千）、元宵、七夕、两周年中文数字：语义保留
  - 补齐除夕（= 腊月最后一日；腊月小建时是廿九，不能写死三十）
  - 修正①：原 JS 版周年用 new Date(y, m, d) 平移，2/29 生日在平年溢出成 3/1；
    此处显式夹到 2/28（与「过生日」习俗一致，也避免日历徽章错位）
  - 修正②：「第 N 天」与「倒计时 N 天」两种口径统一命名，不再混用

sched-share 适配新增
  - next_occurrence()：生日/节日的下一次发生（每年循环，公历或农历）
  - upcoming() / milestone_dates()：给通知系统提前 N 天推送用
"""

from datetime import date, timedelta

__all__ = [
    "DAY_MILESTONES", "SOLAR_FESTIVALS", "LUNAR_FESTIVALS",
    "parse_ymd", "fmt_ymd", "days_between", "day_number", "cn_num",
    "lunar_to_solar", "lunar_new_year", "special_days_in_year",
    "next_special_day", "next_occurrence", "upcoming", "milestone_dates",
]

# ---------------------------------------------------------------- 常量配置

#: 相恋天数里程碑：整百到 1000，520/1314 谐音彩蛋，之后每 1000 天一枚。
#: 「第 N 天」口径：在一起当天 = 第 1 天（与 sched-share 前端 calcAnniv 一致），
#: 因此 N 天纪念日 = 在一起日 + (N-1) 天。
DAY_MILESTONES = [
    *[(i + 1) * 100 for i in range(10)],
    520,
    1314,
    *[(i + 2) * 1000 for i in range(9)],
]

#: 公历固定节日（月, 日, 展示名）
SOLAR_FESTIVALS = [
    (2, 14, "情人节"),
    (5, 20, "520"),
]

#: 农历固定节日（农历月, 农历日, 展示名）
LUNAR_FESTIVALS = [
    (1, 15, "元宵节"),
    (7, 7, "七夕"),
]

# ---------------------------------------------------------------- 基础工具


def parse_ymd(s):
    """'YYYY-MM-DD' → date；date 原样返回；非法返回 None（不抛异常）。"""
    if isinstance(s, date):
        return s
    try:
        p = str(s).strip()[:10].split("-")
        return date(int(p[0]), int(p[1]), int(p[2]))
    except (ValueError, IndexError, AttributeError):
        return None


def fmt_ymd(d):
    """date → 'YYYY-MM-DD'"""
    return "%04d-%02d-%02d" % (d.year, d.month, d.day)


def days_between(frm, to):
    """to - frm（天）。任一端无效返回 None。"""
    a, b = parse_ymd(frm), parse_ymd(to)
    if a is None or b is None:
        return None
    return (b - a).days


def day_number(start, today=None):
    """「在一起第 N 天」：当天 = 第 1 天。start 无效返回 None。"""
    s = parse_ymd(start)
    if s is None:
        return None
    t = parse_ymd(today) if today is not None else date.today()
    return (t - s).days + 1


def _clamp(y, m, d):
    """把日期夹进该年该月最后一天。2/29 平移进平年 → 2/28（不是溢出到 3/1）。"""
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return date(y, m, min(d, (nxt - timedelta(days=1)).day))


_CN_DIGITS = "零一二三四五六七八九"


def cn_num(n):
    """1–99 中文数字；单独的 2 → 「两」（两周年），21/22 组合数仍用「二」。"""
    n = int(n)
    if n == 2:
        return "两"
    if 0 <= n <= 9:
        return _CN_DIGITS[n]
    if n < 10 or n > 99:
        return str(n)
    tens, ones = divmod(n, 10)
    head = "十" if tens == 1 else _CN_DIGITS[tens] + "十"
    return head + (_CN_DIGITS[ones] if ones else "")


# ---------------------------------------------------------------- 农历
# lunarInfo 压缩编码（calendar.js / solarlunar 原始约定）：
#   bit 16     ：闰月大小（1=30天，0=29天）；仅该年有闰月时有意义
#   bits 15-4  ：12 个正常月大小月，1=30天 0=29天（正月 = bit15，即 0x10000>>m）
#   bits 3-1   ：闰月在哪个月（1-12），0 = 该年无闰月
# 覆盖 1900-01-31（1900 年正月初一）起的 201 个农历年，到 2100 年。

_LUNAR_BASE_YEAR = 1900
_LUNAR_BASE_DAY = date(1900, 1, 31)

# —— 真实数据注入区（由 scripts/gen_special_days.py 从 solarlunar 包导出，勿手改）——
_LUNAR = []  # @LUNAR_TABLE_INJECTION_POINT@
_LUNAR = [
    19416, 19168, 42352, 21717, 53856, 55632, 91476, 22176, 39632, 21970,
    19168, 42422, 42192, 53840, 119381, 46400, 54944, 44450, 38320, 84343,
    18800, 42160, 46261, 27216, 27968, 109396, 11104, 38256, 21234, 18800,
    25958, 54432, 59984, 28309, 23248, 11104, 100067, 37600, 116951, 51536,
    54432, 120998, 46416, 22176, 107956, 9680, 37584, 53938, 43344, 46423,
    27808, 46416, 86869, 19872, 42416, 83315, 21168, 43432, 59728, 27296,
    44710, 43856, 19296, 43748, 42352, 21088, 62051, 55632, 23383, 22176,
    38608, 19925, 19152, 42192, 54484, 53840, 54616, 46400, 46752, 103846,
    38320, 18864, 43380, 42160, 45690, 27216, 27968, 44870, 43872, 38256,
    19189, 18800, 25776, 29859, 59984, 27480, 23232, 43872, 38613, 37600,
    51552, 55636, 54432, 55888, 30034, 22176, 43959, 9680, 37584, 51893,
    43344, 46240, 47780, 44368, 21977, 19360, 42416, 86390, 21168, 43312,
    31060, 27296, 44368, 23378, 19296, 42726, 42208, 53856, 60005, 54576,
    23200, 30371, 38608, 19195, 19152, 42192, 118966, 53840, 54560, 56645,
    46496, 22224, 21938, 18864, 42359, 42160, 43600, 111189, 27936, 44448,
    84835, 37744, 18936, 18800, 25776, 92326, 59984, 27424, 108228, 43744,
    37600, 53987, 51552, 54615, 54432, 55888, 23893, 22176, 42704, 21972,
    21200, 43448, 43344, 46240, 46758, 44368, 21920, 43940, 42416, 21168,
    45683, 26928, 29495, 27296, 44368, 84821, 19296, 42352, 21732, 53600,
    59752, 54560, 55968, 92838, 22224, 19168, 43476, 42192, 53584, 62034,
    54560,
]


def _info(lunar_year):
    return _LUNAR[lunar_year - _LUNAR_BASE_YEAR]


def _leap_month(lunar_year):
    """该农历年的闰月是几月（0 = 无闰月）。bits 3-1。"""
    return _info(lunar_year) & 0xF


def _lunar_month_days(lunar_year, lunar_month):
    """某个农历月的天数（29 或 30）。
    lunar_month 传 0 表示该年的闰月本体（须配合调用方已知闰月位置）。
    正常月 m 对应 bit (16-m)：正月 = bit15(0x8000)，腊月 = bit4(0x10)。
    闰月大小看 bit16(0x10000)。"""
    info = _info(lunar_year)
    if lunar_month == 0:
        return 30 if info & 0x10000 else 29
    return 30 if (info >> (16 - lunar_month)) & 0x1 else 29


def _lunar_year_days(lunar_year):
    """该农历年总天数（含闰月）。"""
    info = _info(lunar_year)
    total = 348  # 12 × 29 起算，再逐个加大月的 +1
    for m in range(1, 13):
        total += (info >> (16 - m)) & 0x1
    if info & 0xF:  # 有闰月
        total += 30 if info & 0x10000 else 29
    return total


def lunar_new_year(lunar_year):
    """农历 lunar_year 年正月初一对应的公历 date。支持 1900–2100。"""
    if not (_LUNAR_BASE_YEAR <= lunar_year <= _LUNAR_BASE_YEAR + len(_LUNAR) - 1):
        return None
    days = 0
    for y in range(_LUNAR_BASE_YEAR, lunar_year):
        days += _lunar_year_days(y)
    return _LUNAR_BASE_DAY + timedelta(days=days)


def lunar_to_solar(lunar_year, lunar_month, lunar_day, is_leap_month=False):
    """农历(年, 月, 日) → 公历 date。无效（如该年无此日 / 传了不存在的闰月）返回 None。
    对应原 lunar.ts 的薄封装，范围同为 1900–2100。"""
    if not (_LUNAR_BASE_YEAR <= lunar_year <= _LUNAR_BASE_YEAR + len(_LUNAR) - 1):
        return None
    if not (1 <= lunar_month <= 12 and 1 <= lunar_day <= 30):
        return None
    leap = _leap_month(lunar_year)
    offset = 0
    # 走过整月
    for m in range(1, 13):
        if m == lunar_month and not is_leap_month:
            break
        offset += _lunar_month_days(lunar_year, m)
        if m == lunar_month and is_leap_month:
            break
        if leap == m:  # 走过闰 <m> 月
            offset += _lunar_month_days(lunar_year, 0)
    else:
        return None  # 循环走完没找到目标月（lunar_month > 12 已挡，防御性）
    # 闰月本身的天数校验：目标月的日期不能超过该月天数
    if is_leap_month and leap != lunar_month:
        return None
    month_days = _lunar_month_days(lunar_year, 0 if is_leap_month else lunar_month)
    if lunar_day > month_days:
        return None
    # 目标年正月初一 + 年内偏移 +（日-1）
    ny = lunar_new_year(lunar_year)
    return ny + timedelta(days=offset + lunar_day - 1)


def lunar_month_day_of(solar_day):
    """公历 date → (农历年, 月, 日, 是否闰月)。给「今天农历几号」这类展示用，可选。"""
    if not (isinstance(solar_day, date) and _LUNAR_BASE_DAY <= solar_day):
        return None
    # 找到所在农历年
    y = _LUNAR_BASE_YEAR
    cursor = _LUNAR_BASE_DAY
    while True:
        ydays = _lunar_year_days(y)
        nxt = cursor + timedelta(days=ydays)
        if solar_day < nxt or y >= _LUNAR_BASE_YEAR + len(_LUNAR) - 1:
            break
        cursor = nxt
        y += 1
    delta = (solar_day - cursor).days  # 该农历年内第几天（0 起）
    leap = _leap_month(y)
    m = 1
    while True:
        mdays = _lunar_month_days(y, m)
        if delta < mdays:
            return (y, m, delta + 1, False)
        delta -= mdays
        if leap == m:
            ldays = _lunar_month_days(y, 0)
            if delta < ldays:
                return (y, m, delta + 1, True)
            delta -= ldays
        m += 1


# ---------------------------------------------------------------- 节日与纪念日


def _festival_dates_in_year(year):
    """某公历年内的全部内置节日 → [(date, label, kind), ...]"""
    out = []
    for month, day, label in SOLAR_FESTIVALS:
        out.append((date(year, month, day), label, "festival"))
    ny = lunar_new_year(year)
    if ny is not None:
        for lmonth, lday, label in LUNAR_FESTIVALS:
            # 元宵/七夕各自农历年内都落在同一公历年（春节最早 1/21、最晚 2/20）
            d = lunar_to_solar(year, lmonth, lday)
            if d is not None:
                out.append((d, label, "festival"))
        # 除夕 = 「当年正月初一」的前一天：它属于上一个农历年的腊月末，
        # 但公历上必然落在本年内（正月初一最早 1/21 → 除夕最早 1/20）
        out.append((ny - timedelta(days=1), "除夕", "festival"))
    return out


def special_days_in_year(year, start_date):
    """一年里值得贴在日历上的日子。
    从在一起纪念日推天数里程碑（100天/520/1314/…）与周年，再叠上节日。
    返回 [{'date': 'YYYY-MM-DD', 'label': str, 'kind': 'milestone'|'anniversary'|'festival'}]
    按 date 升序。start_date 无效返回 []。"""
    start = parse_ymd(start_date)
    if start is None:
        return []
    out = []
    for n in DAY_MILESTONES:
        d = start + timedelta(days=n - 1)
        if d.year != year:
            continue
        label = "1314 天 · 一生一世" if n == 1314 else "%d 天纪念日" % n
        out.append({"date": fmt_ymd(d), "label": label, "kind": "milestone"})
    years = year - start.year
    if years >= 1:
        # 修正①：夹住月末 —— 2/29 在一起 → 平年的「一周年」落在 2/28
        d = _clamp(year, start.month, start.day)
        out.append({"date": fmt_ymd(d), "label": "%s周年" % cn_num(years), "kind": "anniversary"})
    for d, label, kind in _festival_dates_in_year(year):
        out.append({"date": fmt_ymd(d), "label": label, "kind": kind})
    out.sort(key=lambda x: x["date"])
    return out


def next_special_day(today, start_date):
    """距今最近的一个未来日子（含今天）。找不到返回 None。"""
    t = parse_ymd(today)
    if t is None:
        return None
    y0 = t.year
    all_days = []
    for y in range(y0 - 1, y0 + 3):
        all_days.extend(special_days_in_year(y, start_date))
    future = sorted((d for d in all_days if d["date"] >= fmt_ymd(t)), key=lambda x: x["date"])
    return future[0] if future else None


# ------------------------------------------------ sched-share 适配：生日循环


def _years_spanned(base, target):
    """从 base 到 target 经过的整年数（周岁/周年口径，月日比较，不受 2/29 影响）。"""
    if target < base:
        return 0
    n = target.year - base.year
    if (target.month, target.day) < (base.month, base.day):
        n -= 1
    return n


def next_occurrence(anchor, today, kind="solar"):
    """每年循环的下一次发生（生日 / 周年用）。

    anchor : kind='solar' → 'YYYY-MM-DD'（出生日 / 在一起日）
             kind='lunar' → (农历月, 农历日) 或 (农历出生年, 农历月, 农历日)
    today  : 'YYYY-MM-DD' 或 date
    kind   : 'solar' 按公历月日循环 | 'lunar' 按农历月日循环（农历生日）

    返回 dict：
      date      : date，下一次发生的公历日期
      days_left : int，距今天数（0 = 就是今天）
      turns     : int | None，周岁 / 第几周年。
                  口径：到达目标日当天 = turns 满值（1997-04-30 出生 →
                  2027-04-30 turns=30；2027-05-01 起下一次 turns=31）。
                  anchor 只给月日（无年份意义）时返回 None——
                  sched-share 现在存的生日 date 可能就是「下一个生日」而非出生年。
      is_today  : bool
    anchor 或 today 无效返回 None。
    """
    t = parse_ymd(today)
    if t is None:
        return None
    if kind == "lunar":
        try:
            parts = tuple(anchor)
        except (TypeError, ValueError):
            return None
        if len(parts) == 3:
            base_year, lmonth, lday = parts
        elif len(parts) == 2:
            base_year, (lmonth, lday) = None, parts
        else:
            return None
        if not (1 <= lmonth <= 12 and 1 <= lday <= 30):
            return None
        # 农历腊月落在次年公历年初，扫描范围需从 t.year-1 起
        for year in range(t.year - 1, t.year + 2):
            d = lunar_to_solar(year, lmonth, lday)
            if d is None:  # 该农历年无此日（闰月传错 / 腊月小建无三十）
                continue
            if d >= t:
                # 农历生日每年必发生一次（除非该农历年的目标月被压缩，极罕见），
                # 周岁 = 农历年差，无需月日比较
                turns = (year - base_year) if base_year is not None else None
                return {"date": d, "days_left": (d - t).days,
                        "turns": turns, "is_today": d == t}
        return None

    a = parse_ymd(anchor)
    if a is None:
        return None
    for year in (t.year, t.year + 1):
        d = _clamp(year, a.month, a.day)
        if d >= t:
            return {"date": d, "days_left": (d - t).days,
                    "turns": _years_spanned(a, d), "is_today": d == t}
    return None


def anniversary_date(start, n_days):
    """在一起第 n_days 天的公历日期（当天 = 第 1 天）。"""
    s = parse_ymd(start)
    return None if s is None else s + timedelta(days=n_days - 1)


def upcoming(items, today, days=30):
    """给通知系统的入口：today 起 days 天内将要发生的日子，按日期升序。

    items : [{'id':…, 'title':…, 'date':'YYYY-MM-DD',
              'target_type':'love'|'birthday'|'countdown'}, ...]
            （与 sched-share 的 anniversaries 表行兼容；额外允许 'repeat':'lunar'
             表示农历生日，'date' 字段此时忽略月日、只用其年份意义可无）
    today : 'YYYY-MM-DD' 或 date

    返回 [{'id','title','target_type','date'(发生日),'days_left','label'}, ...]
    其中 love 类型还附带 'day_number'（第 N 天）。
    """
    t = parse_ymd(today)
    if t is None:
        return []
    horizon = t + timedelta(days=days)
    out = []
    for it in items:
        title = it.get("title", "")
        ttype = it.get("target_type", "love")
        aid = it.get("id")
        if ttype == "countdown":
            d = parse_ymd(it.get("date"))
            if d is None or not (t <= d <= horizon):
                continue
            left = (d - t).days
            out.append({"id": aid, "title": title, "target_type": ttype,
                        "date": fmt_ymd(d), "days_left": left,
                        "label": "「%s」就是今天啦！" % title if left == 0
                        else "「%s」还有 %d 天" % (title, left)})
            continue
        if ttype == "love":
            s = parse_ymd(it.get("date"))
            if s is None:
                continue
            for n in DAY_MILESTONES:
                d = s + timedelta(days=n - 1)
                if not (t <= d <= horizon):
                    continue
                left = (d - t).days
                what = "一生一世纪念日" if n == 1314 else "在一起第 %d 天" % n
                out.append({"id": aid, "title": title, "target_type": ttype,
                            "date": fmt_ymd(d), "days_left": left,
                            "day_number": n,
                            "label": "今天是「%s」%s！" % (title, what) if left == 0
                            else "%d 天后%s（%s）" % (left, what, title)})
            continue
        # birthday：默认公历循环，'repeat':'lunar' 走农历（anchor 带年份时给出岁数）
        rep = it.get("repeat", "solar")
        if rep == "lunar":
            lm, ld = it.get("lunar_month", 0), it.get("lunar_day", 0)
            occ = next_occurrence((lm, ld), t, kind="lunar")
        else:
            occ = next_occurrence(it.get("date"), t, kind="solar")
        if occ is None or occ["date"] > horizon:
            continue
        turns = occ.get("turns")
        has_bd = "生日" in title
        if turns and turns > 0:
            age = "%s 岁" % turns if has_bd else "%s 岁生日" % turns
        elif has_bd:
            age = ""
        else:
            age = "生日"
        quoted = "「%s」%s" % (title, age)
        label = ("今天是%s！🎉" % quoted) if occ["is_today"] \
            else ("%d 天后%s" % (occ["days_left"], quoted))
        out.append({"id": aid, "title": title, "target_type": ttype,
                    "date": fmt_ymd(occ["date"]), "days_left": occ["days_left"],
                    "turns": turns, "label": label})
    out.sort(key=lambda x: (x["date"], str(x["title"])))
    return out


def milestone_dates(start_date, today, days=7):
    """相恋天数里程碑的提前提醒（love 专用，30/100/520 这种容易忘）。
    返回 upcoming() 同构 dict 列表。"""
    items = [{"id": None, "title": "在一起", "date": start_date, "target_type": "love"}]
    return [x for x in upcoming(items, today, days=days)]
