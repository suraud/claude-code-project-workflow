---
name: 00-board
description: 全プロジェクトの、利用者が動く・待つタスクを 1 枚にまとめたタスクボードを作り直して、ブラウザで開く。
disable-model-invocation: true
---

# タスクボード

各プロジェクトの `docs/00_前提/タスクボード.md` を集めて、1 枚の HTML(`~/.claude/board/board.html`)にし、ブラウザで開く。ボードは見るだけのもので、タスクの追加や変更は、各プロジェクトのセッションが `タスクボード.md` を書き換えて行う。

## 手順

次のコマンドを実行する。ボードを作り直して、ブラウザで開く。

```
~/.claude/skills/00-board/scripts/board.py open
```

実行したら、開いたことと、「いま対応する」の件数を一言で伝える。

## 仕組み

- 対象は、`~/.claude/kickoff-projects.txt` にあるプロジェクト(`/00-kickoff` で始めたもの)のうち、`タスクボード.md` があるものだけ
- スクリプトが読むのは各プロジェクトの `タスクボード.md` だけで、書くのは `~/.claude/board/` だけ。外部のライブラリも、外部の読み込みも使わない
- ボードは、開いたままで自分で読み直す。ブラウザのブックマークに入れておけば、このスキルを呼ばなくても開ける
- `タスクボード.md` の書き方と、更新する時機は、このフォルダの `format.md` にある

## 列

| 列 | 入るもの |
|---|---|
| いま対応する | 期限が今日まで、または過ぎたカード。どの節のカードも、期限が来ると自動でここに移る |
| 自分の番 | 「自分がやる・決める」の節 |
| 相手の番 | 「相手の回答待ち」の節 |
| 手が空いたら | 「手が空いたら」の節 |

## hook

ボードを自動で作り直すには、`~/.claude/settings.json` の `hooks` に次の 2 つを足す。足さない場合は、このスキルを呼んだときだけ作り直される。

```json
"PostToolUse": [
  {
    "matcher": "Edit|Write|MultiEdit|Bash",
    "hooks": [
      { "type": "command", "command": "~/.claude/skills/00-board/scripts/board.py hook-edit", "timeout": 10 }
    ]
  }
],
"SessionStart": [
  {
    "hooks": [
      { "type": "command", "command": "~/.claude/skills/00-board/scripts/board.py hook-start", "timeout": 10 }
    ]
  }
]
```

- `hook-edit`: `タスクボード.md` がボードより新しいとき、またはボードが前の日のものであるときだけ、ボードを作り直す。作り直したら、「タスクボードを作り直しました」と Claude に知らせる。それ以外のときは何もしない
- `hook-start`: セッションの最初にボードを作り直す。そのプロジェクトのカードの一覧(番号、節、件名、期限)を Claude に渡す。期限を過ぎたカードがあれば、利用者に伝えるよう指示する
