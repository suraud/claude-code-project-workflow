#!/usr/bin/env python3
"""タスクボード: 各プロジェクトの docs/00_前提/タスクボード.md を集めて、1 枚の HTML にする。

使い方:
  board.py build        ボードを作り直す
  board.py open         作り直して、ブラウザで開く
  board.py hook-edit    PostToolUse hook 用。タスクボード.md がボードより新しいとき、
                        またはボードが前の日のものであるときだけ作り直し、Claude に知らせる
  board.py hook-start   SessionStart hook 用。作り直し、このプロジェクトのカードの一覧を Claude に渡す

対象のプロジェクトは ~/.claude/kickoff-projects.txt(1 行 1 つの絶対パス)にあるものだけ。
読むのは各プロジェクトの タスクボード.md だけで、書くのは ~/.claude/board/ だけ。
外部のライブラリは使わない。
"""
import datetime
import html
import json
import os
import re
import subprocess
import sys
import tempfile

HOME = os.path.expanduser("~")
PROJECTS_FILE = os.environ.get("BOARD_PROJECTS", os.path.join(HOME, ".claude", "kickoff-projects.txt"))
OUT_FILE = os.environ.get("BOARD_OUT", os.path.join(HOME, ".claude", "board", "board.html"))
SOURCE_REL = os.path.join("docs", "00_前提", "タスクボード.md")
RELOAD_SECONDS = 60  # 開いたままのボードが自分で読み直す間隔

# 正本の節。見出しにこの語が含まれていれば、その節として扱う
SECTIONS = [
    ("wait", "回答待ち"),
    ("do", "やる"),
    ("ask", "質問"),
    ("later", "手が空いたら"),
]
# ボードの区画。最初の「いま対応する」は上の段に置き、期限が今日までのカードが、節に関係なく集まる。
# 残りは、下の段に列として並べる
COLUMNS = [
    ("now", "いま対応する", "期限が今日まで、または過ぎたもの"),
    ("do", "自分の番", "自分がやる・決める"),
    ("ask", "質問に答える", "Claude からの質問"),
    ("wait", "相手の番", "相手の回答待ち"),
    ("later", "手が空いたら", "急がないもの"),
]
ORIGIN_LABEL = {"wait": "相手の番 → 催促する", "do": "自分の番", "ask": "質問に答える", "later": "手が空いたら"}
SECTION_NAMES = {"wait": "相手の回答待ち", "do": "自分がやる・決める", "ask": "Claude からの質問", "later": "手が空いたら"}
FIELD_ORDER = ["相手", "場所", "期限", "最後の動き", "次にやること", "メモ"]
DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
URL_RE = re.compile(r"https?://[^\s<>\"'）)」]+")
WEEKDAYS = "月火水木金土日"


def find_date(text):
    m = DATE_RE.search(text or "")
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def read_projects():
    try:
        with open(PROJECTS_FILE, encoding="utf-8") as f:
            lines = [line.strip() for line in f]
    except OSError:
        return []
    return [p for p in lines if p and not p.startswith("#")]


def parse(path):
    """タスクボード.md を読んで、カードの一覧を返す。"""
    cards = []
    column = None
    card = None
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return cards
    in_comment = False
    for line in lines:
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if line.lstrip().startswith("<!--"):
            if "-->" not in line:
                in_comment = True
            continue
        if line.startswith("#"):
            card = None
            column = None
            if line.startswith("## "):
                for key, word in SECTIONS:
                    if word in line:
                        column = key
                        break
            continue
        if column is None or not line.strip():
            continue
        m = re.match(r"^[-*]\s+(?:\[[ xX]\]\s+)?(.+)$", line)
        if m:
            title = m.group(1).strip()
            no = None
            n = re.match(r"^#(\d+)\s+(.+)$", title)
            if n:
                no, title = int(n.group(1)), n.group(2).strip()
            card = {"column": column, "no": no, "title": title, "fields": {}, "notes": []}
            cards.append(card)
            continue
        m = re.match(r"^\s+[-*]\s+(.+)$", line)
        if m and card is not None:
            body = m.group(1).strip()
            kv = re.match(r"^([^::]{1,12})[::]\s*(.*)$", body)
            if kv and kv.group(1).strip() in FIELD_ORDER:
                card["fields"][kv.group(1).strip()] = kv.group(2).strip()
            else:
                card["notes"].append(body)
    return cards


def collect():
    """全プロジェクトのカードを集める。"""
    projects = []
    for root in read_projects():
        src = os.path.join(root, SOURCE_REL)
        if not os.path.isfile(src):
            continue
        name = os.path.basename(root.rstrip("/"))
        cards = parse(src)
        for c in cards:
            c["project"] = name
            c["due"] = find_date(c["fields"].get("期限"))
            c["moved"] = find_date(c["fields"].get("最後の動き"))
        projects.append({"name": name, "root": root, "cards": cards})
    return projects


def is_late(card, today):
    return card["due"] is not None and card["due"] < today


def is_now(card, today):
    return card["due"] is not None and card["due"] <= today


def board_column(card, today):
    return "now" if is_now(card, today) else card["column"]


def esc(text):
    return html.escape(text or "", quote=True)


def linkify(text):
    out = []
    pos = 0
    for m in URL_RE.finditer(text or ""):
        out.append(esc(text[pos:m.start()]))
        url = m.group(0)
        out.append('<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(url), esc(url)))
        pos = m.end()
    out.append(esc((text or "")[pos:]))
    return "".join(out)


def due_label(card, today):
    due = card["due"]
    raw = card["fields"].get("期限")
    if due is None:
        # 期限の行がないものも、「未設定」と書いたものも、同じ表示にそろえる
        if card["column"] == "wait":
            return '<span class="due late">期限 未設定(催促する日を決める)</span>'
        if raw and raw != "未設定":
            return '<span class="nodue">期限 %s</span>' % esc(raw)
        return '<span class="nodue">期限 未設定</span>'
    days = (due - today).days
    text = "期限 %d/%d(%s)" % (due.month, due.day, WEEKDAYS[due.weekday()])
    if days < 0:
        return '<span class="due late">%s ・ %d 日超過</span>' % (text, -days)
    if days == 0:
        return '<span class="due late">%s ・ 今日</span>' % text
    return '<span class="due">%s ・ あと %d 日</span>' % (text, days)


def moved_label(card, today):
    raw = card["fields"].get("最後の動き")
    if not raw:
        return ""
    moved = card["moved"]
    ago = ""
    if moved is not None:
        days = (today - moved).days
        ago = "(今日)" if days == 0 else "(%d 日前)" % days if days > 0 else ""
    return "最後の動き: %s %s" % (linkify(raw), ago)


def render_card(card, today):
    f = card["fields"]
    late = " is-late" if is_late(card, today) else ""
    # 閉じていても見えるのは、件名と、だれの番か・期限だけ。残りは、カードを押すと開く
    head = []
    if is_now(card, today):
        head.append('<span class="origin">%s</span>' % esc(ORIGIN_LABEL[card["column"]]))
    due = due_label(card, today)
    if due:
        head.append(due)
    rows = []
    who = " / ".join(linkify(f[k]) for k in ("相手", "場所") if f.get(k))
    if who:
        rows.append(who)
    moved = moved_label(card, today)
    if moved:
        rows.append(moved)
    if f.get("次にやること"):
        rows.append("次にやること: " + linkify(f["次にやること"]))
    if f.get("メモ"):
        rows.append(linkify(f["メモ"]))
    rows.extend(linkify(n) for n in card["notes"])
    no = '<span class="no">%d 番</span>' % card["no"] if card["no"] is not None else ""
    top = '<span class="proj">%s</span>%s<b>%s</b>' % (esc(card["project"]), no, linkify(card["title"]))
    if head:
        top += '<span class="meta">%s</span>' % " ・ ".join(head)
    attrs = 'class="card%s" data-project="%s"' % (late, esc(card["project"]))
    if not rows:
        return "<div %s>%s</div>" % (attrs, top)
    key = "%s#%s" % (card["project"], card["no"] if card["no"] is not None else card["title"])
    more = "".join('<div class="meta">%s</div>' % r for r in rows)
    return '<details %s data-key="%s"><summary>%s</summary><div class="more">%s</div></details>' % (
        attrs, esc(key), top, more)


def sort_key(card):
    # 期限のあるものを期限の早い順に、期限のないものはそのあとに、書かれた順で並べる
    return (0, card["due"].toordinal()) if card["due"] else (1, 0)


CSS = """
:root{--bg:#f6f8fa;--card:#fff;--fg:#1f2328;--sub:#57606a;--line:#d0d7de;--now:#c0392b;--wait:#b8641a;--do:#0969da;--ask:#8250df;--later:#57606a;--late:#c0392b;--latebg:#fdecea}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--fg:#e6edf3;--sub:#9da7b3;--line:#30363d;--now:#e0675a;--wait:#e09a4f;--do:#4493f8;--ask:#a371f7;--later:#9da7b3;--late:#e0675a;--latebg:#3a1d1a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;font-size:14px;line-height:1.6}
main{max-width:1280px;margin:0 auto;padding:20px 16px 48px}
h1{font-size:1.3em;margin:0 0 4px}
.sub{color:var(--sub);font-size:.9em;margin:0 0 12px}
.filters{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 16px}
.filters .gap{flex:1}
.filters button{font:inherit;font-size:.9em;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:999px;padding:3px 12px;cursor:pointer}
.filters button.on{background:var(--do);border-color:var(--do);color:#fff}
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;align-items:start}
.top{margin:0 0 20px}
.top .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:0 14px;align-items:start}
.col h2{font-size:1em;margin:0;padding-bottom:2px}
.col .desc{color:var(--sub);font-size:.82em;margin:0 0 8px;padding-bottom:6px;border-bottom:3px solid var(--c)}
.origin{font-weight:700;color:var(--late)}
.col h2 span{color:var(--sub);font-weight:400;font-size:.9em}
.card{display:block;background:var(--card);border:1px solid var(--line);border-left:4px solid var(--c);border-radius:6px;padding:7px 10px;margin-bottom:8px;overflow-wrap:anywhere}
.card summary{cursor:pointer;list-style:none}
.card summary::-webkit-details-marker{display:none}
.card summary b::after{content:" ▸";color:var(--sub);font-weight:400}
.card[open] summary b::after{content:" ▾"}
.card .more{margin-top:6px;padding-top:6px;border-top:1px dashed var(--line)}
.card .more .meta+.meta{margin-top:2px}
.card.is-late{border-color:var(--late);border-left-color:var(--late);background:var(--latebg)}
.card b{display:block}
.proj{display:inline-block;font-size:.78em;color:var(--sub);border:1px solid var(--line);border-radius:4px;padding:0 6px;margin-bottom:4px}
.no{display:inline-block;font-size:.78em;font-weight:700;color:var(--c);border:1px solid var(--c);border-radius:4px;padding:0 6px;margin:0 0 4px 6px}
.card.is-late .no{color:var(--late);border-color:var(--late)}
.meta{display:block;color:var(--sub);font-size:.88em}
.due{font-weight:700;color:var(--c)}
.due.late{color:var(--late)}
.empty{color:var(--sub);font-size:.9em}
a{color:var(--do)}
"""

JS = """
(function(){
  var KEY='board-project';
  var buttons=document.querySelectorAll('.filters button[data-project]');
  function load(){try{return localStorage.getItem(KEY)||''}catch(e){return ''}}
  function save(v){try{localStorage.setItem(KEY,v)}catch(e){}}
  function apply(name){
    buttons.forEach(function(b){b.classList.toggle('on',b.dataset.project===name)});
    document.querySelectorAll('.card').forEach(function(c){
      c.style.display=(!name||c.dataset.project===name)?'':'none';
    });
    document.querySelectorAll('.col').forEach(function(col){
      var n=0;col.querySelectorAll('.card').forEach(function(c){if(c.style.display!=='none')n++});
      col.querySelector('h2 span').textContent=n+' 件';
      var e=col.querySelector('.empty');if(e)e.style.display=n?'none':'';
    });
  }
  function pick(name){
    save(name);
    try{history.replaceState(null,'',name?'#'+encodeURIComponent(name):location.pathname)}catch(e){}
    apply(name);
  }
  buttons.forEach(function(b){b.addEventListener('click',function(){pick(b.dataset.project)})});
  var want='';
  try{want=decodeURIComponent(location.hash.slice(1))}catch(e){}
  if(!want)want=load();
  var known=false;buttons.forEach(function(b){if(b.dataset.project===want)known=true});
  apply(known?want:'');
  // 開いたカードを覚えておき、読み直しても開いたままにする
  var OPEN='board-open';
  var cards=document.querySelectorAll('details.card');
  function opened(){try{return JSON.parse(localStorage.getItem(OPEN)||'[]')}catch(e){return []}}
  function remember(){
    var keys=[];cards.forEach(function(d){if(d.open)keys.push(d.dataset.key)});
    try{localStorage.setItem(OPEN,JSON.stringify(keys))}catch(e){}
  }
  var was=opened();
  cards.forEach(function(d){
    if(was.indexOf(d.dataset.key)>=0)d.open=true;
    d.addEventListener('toggle',remember);
  });
  document.querySelectorAll('button[data-open]').forEach(function(b){
    b.addEventListener('click',function(){
      var on=b.dataset.open==='1';
      cards.forEach(function(d){if(d.style.display!=='none')d.open=on});
    });
  });
  // 開いたままで読み直す。絞り込みは、アドレスの # と保存した値の両方から戻す
  setTimeout(function(){location.reload()},RELOAD_MS);
})();
"""


def render(projects, now):
    today = now.date()
    cards = [c for p in projects for c in p["cards"]]
    parts = [
        '<!doctype html>\n<html lang="ja"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>タスクボード</title><style>%s</style></head><body><main>" % CSS,
        "<h1>タスクボード</h1>",
        '<p class="sub">%s 時点 ・ %d プロジェクト ・ %d 件。カードを押すと、詳細が開きます。開いたままで、%d 秒ごとに読み直します。</p>' % (
            now.strftime("%Y-%m-%d %H:%M"), len(projects), len(cards), RELOAD_SECONDS),
    ]
    buttons = []
    if len(projects) > 1:
        buttons.append('<button data-project="">すべて</button>')
        buttons += ['<button data-project="%s">%s</button>' % (esc(p["name"]), esc(p["name"])) for p in projects]
    if cards:
        buttons.append('<span class="gap"></span><button data-open="1">詳細を開く</button>'
                       '<button data-open="0">詳細を閉じる</button>')
    if buttons:
        parts.append('<div class="filters">%s</div>' % "".join(buttons))
    if not projects:
        parts.append('<p class="empty">タスクボード.md のあるプロジェクトが、まだありません。</p>')
    for i, (key, title, desc) in enumerate(COLUMNS):
        col = sorted((c for c in cards if board_column(c, today) == key), key=sort_key)
        body = "".join(render_card(c, today) for c in col)
        body += '<p class="empty"%s>なし</p>' % (' style="display:none"' if col else "")
        parts.append('<section class="col%s" style="--c:var(--%s)"><h2>%s <span>%d 件</span></h2>'
                     '<p class="desc">%s</p><div class="cards">%s</div></section>' % (
                         " top" if i == 0 else "", key, title, len(col), desc, body))
        if i == 0:
            parts.append('<div class="cols">')
    script = JS.replace("RELOAD_MS", str(RELOAD_SECONDS * 1000))
    parts.append("</div></main><script>%s</script></body></html>\n" % script)
    return "\n".join(parts)


def build():
    projects = collect()
    text = render(projects, datetime.datetime.now())
    out_dir = os.path.dirname(OUT_FILE)
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=out_dir, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, OUT_FILE)
    return projects


def read_hook_input():
    try:
        return json.load(sys.stdin)
    except (ValueError, OSError):
        return {}


def is_stale():
    """ボードを作り直す必要があるか。正本がボードより新しい、またはボードが前の日のもの。"""
    try:
        built = os.path.getmtime(OUT_FILE)
    except OSError:
        return True
    if datetime.date.fromtimestamp(built) != datetime.date.today():
        return True
    try:
        if os.path.getmtime(PROJECTS_FILE) > built:
            return True
    except OSError:
        pass
    for root in read_projects():
        try:
            if os.path.getmtime(os.path.join(root, SOURCE_REL)) > built:
                return True
        except OSError:
            continue
    return False


def hook_edit():
    read_hook_input()
    if not is_stale():
        return
    projects = build()
    msg = "タスクボードを作り直しました(%d プロジェクト、%d 件)。" % (
        len(projects), sum(len(p["cards"]) for p in projects))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}},
                     ensure_ascii=False))


def hook_start():
    data = read_hook_input()
    projects = build()
    cwd = os.path.realpath(data.get("cwd") or os.getcwd())
    today = datetime.date.today()
    for p in projects:
        root = os.path.realpath(p["root"])
        if cwd != root and not cwd.startswith(root + os.sep):
            continue
        if not p["cards"]:
            return
        lines = []
        for c in p["cards"]:
            no = "%d 番" % c["no"] if c["no"] is not None else "番号なし"
            if c["due"] is None:
                due = "期限なし"
            elif is_late(c, today):
                due = "期限 %s、%d 日超過" % (c["due"].isoformat(), (today - c["due"]).days)
            else:
                due = "期限 %s" % c["due"].isoformat()
            lines.append("- %s [%s] %s(%s)" % (no, SECTION_NAMES[c["column"]], c["title"], due))
        msg = ("このプロジェクトのタスクボード(%s)のカードです。利用者が動く・待つことの一覧で、"
               "書き換えの決まりは ~/.claude/skills/00-board/format.md にあります。"
               "作業の中で、返事が来た・片づいた・増えた、に気づいたら書き換えてください。\n%s") % (
                   SOURCE_REL, "\n".join(lines))
        late = [c for c in p["cards"] if is_late(c, today)]
        if late:
            msg += ("\n期限を過ぎたカードが %d 件あります。最初の応答で利用者に伝え、"
                    "どうするか(催促する、期限を延ばす、片づいたので消す)を尋ねてください。") % len(late)
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": msg}},
                         ensure_ascii=False))
        return


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        projects = build()
        print("作り直しました: %s(%d プロジェクト、%d 件)" % (
            OUT_FILE, len(projects), sum(len(p["cards"]) for p in projects)))
    elif cmd == "open":
        projects = build()
        subprocess.run(["open", OUT_FILE], check=False)
        today = datetime.date.today()
        cards = [c for p in projects for c in p["cards"]]
        print("開きました: %s(いま対応する %d 件、全体 %d 件)" % (
            OUT_FILE, sum(1 for c in cards if is_now(c, today)), len(cards)))
    elif cmd == "hook-edit":
        hook_edit()
    elif cmd == "hook-start":
        hook_start()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # hook がセッションを止めないようにする
        print("board.py: %s" % e, file=sys.stderr)
        sys.exit(0 if len(sys.argv) > 1 and sys.argv[1].startswith("hook-") else 1)
