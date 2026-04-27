#!/usr/bin/env python3
"""
english-cli — CLI英語学習ツール
rustlings風 × Claude AI添削 × SRS単語復習

使い方:
  english-cli study       今日の学習を開始
  english-cli write       英作文 + Claude添削
  english-cli grammar     文法ドリル
  english-cli review      単語SRSレビュー
  english-cli stats       学習統計
  english-cli config      API設定
"""

import os
import sys
import json
import sqlite3
import random
import time
import textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn
from rich import box
from rich.text import Text
from rich.rule import Rule
from rich.prompt import Prompt, Confirm

# ── グローバル ─────────────────────────────────────────────────────────────────

console = Console()
APP_NAME = "english-cli"


def get_db_path() -> Path:
    data_dir = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    db_dir = data_dir / "english-cli"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "progress.db"


# ── データベース ────────────────────────────────────────────────────────────────

class Database:
    def __init__(self):
        self.conn = sqlite3.connect(str(get_db_path()))
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        self.conn.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS vocab_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT NOT NULL UNIQUE,
                meaning_ja TEXT NOT NULL,
                example TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'b1',
                ease_factor REAL NOT NULL DEFAULT 2.5,
                interval_days INTEGER NOT NULL DEFAULT 1,
                due_date TEXT NOT NULL,
                reviews INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS study_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                mode TEXT NOT NULL,
                score REAL NOT NULL,
                errors TEXT NOT NULL DEFAULT '[]',
                duration_secs INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS writing_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                topic TEXT NOT NULL,
                user_text TEXT NOT NULL,
                feedback TEXT NOT NULL DEFAULT '{}',
                grammar_score REAL NOT NULL DEFAULT 0,
                naturalness_score REAL NOT NULL DEFAULT 0,
                vocabulary_score REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS error_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                error_type TEXT NOT NULL UNIQUE,
                count INTEGER NOT NULL DEFAULT 1,
                last_seen TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self.conn.commit()

        # 単語が空なら初期データ投入
        count = self.conn.execute("SELECT COUNT(*) FROM vocab_cards").fetchone()[0]
        if count == 0:
            self._seed_vocab()

    def _seed_vocab(self):
        now = datetime.now(timezone.utc).isoformat()
        vocab = [
            # A2
            ("however",       "しかしながら",         "I wanted to go; however, it was raining.",           "a2"),
            ("although",      "〜だけれども",           "Although she was tired, she kept working.",           "a2"),
            ("perhaps",       "おそらく",              "Perhaps we should try a different approach.",         "a2"),
            # B1
            ("therefore",     "したがって",            "It rained; therefore, the game was canceled.",        "b1"),
            ("consequently",  "その結果として",         "He didn't study; consequently, he failed.",           "b1"),
            ("furthermore",   "さらに",                "She is smart; furthermore, she works hard.",          "b1"),
            ("meanwhile",     "その間に",              "I'll cook; meanwhile, you can set the table.",        "b1"),
            # B2
            ("nevertheless",  "それにもかかわらず",     "It was cold; nevertheless, they went out.",           "b2"),
            ("whereas",       "〜である一方で",         "He likes coffee, whereas she prefers tea.",           "b2"),
            ("ubiquitous",    "至る所にある",           "Smartphones have become ubiquitous.",                 "b2"),
            ("pragmatic",     "実用的な",              "We need a pragmatic approach to the problem.",        "b2"),
            # C1
            ("albeit",        "〜ではあるが",           "It was a small, albeit important, victory.",          "c1"),
            ("notwithstanding","〜にもかかわらず",      "Notwithstanding the risks, they proceeded.",         "c1"),
            ("juxtapose",     "並置する",              "The artist juxtaposed light and dark colors.",        "c1"),
            ("eloquent",      "雄弁な",                "She gave an eloquent and moving speech.",             "c1"),
            # C2
            ("ameliorate",    "改善する",              "New policies aim to ameliorate poverty.",             "c2"),
            ("hitherto",      "これまでは",            "Hitherto unknown facts came to light.",               "c2"),
            ("perspicacious", "洞察力のある",           "The perspicacious analyst spotted the flaw.",        "c2"),
        ]
        self.conn.executemany(
            "INSERT OR IGNORE INTO vocab_cards (word, meaning_ja, example, level, due_date) VALUES (?,?,?,?,?)",
            [(w, m, e, l, now) for w, m, e, l in vocab]
        )
        self.conn.commit()

    # SRS: SM-2アルゴリズム
    def get_due_cards(self, limit: int = 20):
        now = datetime.now(timezone.utc).isoformat()
        return self.conn.execute(
            "SELECT * FROM vocab_cards WHERE due_date <= ? ORDER BY due_date LIMIT ?",
            (now, limit)
        ).fetchall()

    def update_card_srs(self, card_id: int, ease_factor: float, interval_days: int, quality: int):
        new_ef = max(1.3, ease_factor + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        if quality < 3:
            new_interval = 1
        else:
            new_interval = max(1, round(interval_days * new_ef))
        due = (datetime.now(timezone.utc) + timedelta(days=new_interval)).isoformat()
        self.conn.execute(
            "UPDATE vocab_cards SET ease_factor=?, interval_days=?, due_date=?, reviews=reviews+1 WHERE id=?",
            (new_ef, new_interval, due, card_id)
        )
        self.conn.commit()

    def save_session(self, mode: str, score: float, duration: int, errors: list):
        self.conn.execute(
            "INSERT INTO study_sessions (date, mode, score, errors, duration_secs) VALUES (?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), mode, score, json.dumps(errors), duration)
        )
        self.conn.commit()

    def save_writing(self, topic: str, text: str, feedback: dict):
        self.conn.execute(
            "INSERT INTO writing_entries (date, topic, user_text, feedback, grammar_score, naturalness_score, vocabulary_score) VALUES (?,?,?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(), topic, text,
                json.dumps(feedback, ensure_ascii=False),
                feedback.get("grammar_score", 0),
                feedback.get("naturalness_score", 0),
                feedback.get("vocabulary_score", 0),
            )
        )
        self.conn.commit()

    def record_error(self, error_type: str):
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO error_patterns (error_type, count, last_seen) VALUES (?, 1, ?) "
            "ON CONFLICT(error_type) DO UPDATE SET count=count+1, last_seen=?",
            (error_type, now, now)
        )
        self.conn.commit()

    def get_stats(self) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        total_sessions = self.conn.execute("SELECT COUNT(*) FROM study_sessions").fetchone()[0]
        avg_score = self.conn.execute("SELECT COALESCE(AVG(score),0) FROM study_sessions").fetchone()[0]
        total_writing = self.conn.execute("SELECT COUNT(*) FROM writing_entries").fetchone()[0]
        vocab_learned = self.conn.execute("SELECT COUNT(*) FROM vocab_cards WHERE reviews > 0").fetchone()[0]
        due_today = self.conn.execute("SELECT COUNT(*) FROM vocab_cards WHERE due_date <= ?", (now,)).fetchone()[0]
        top_errors = self.conn.execute(
            "SELECT error_type, count FROM error_patterns ORDER BY count DESC LIMIT 5"
        ).fetchall()
        return {
            "total_sessions": total_sessions,
            "avg_score": avg_score,
            "total_writing": total_writing,
            "vocab_learned": vocab_learned,
            "due_today": due_today,
            "top_errors": [(r["error_type"], r["count"]) for r in top_errors],
        }

    def get_config(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_config(self, key: str, value: str):
        self.conn.execute(
            "INSERT INTO config (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=?",
            (key, value, value)
        )
        self.conn.commit()


# ── 問題データ ──────────────────────────────────────────────────────────────────

EXERCISES = [

    # ════════════════════════════════════════
    # A2 — 基礎文法
    # ════════════════════════════════════════

    # 冠詞
    {"id":"a2-art-01","level":"a2","category":"冠詞",
     "instruction":"空欄に適切な冠詞（a / an / the）を入れてください",
     "prompt":"I saw ___ elephant at the zoo yesterday.",
     "answer":"an","alternatives":[],
     "hint":"母音で始まる名詞の前には？",
     "explanation":"elephant は母音 'e' で始まるため 'an' を使います。発音が母音で始まる場合も同様（an hour）。",
     "error_tags":["article"]},

    {"id":"a2-art-02","level":"a2","category":"冠詞",
     "instruction":"空欄に適切な冠詞（a / an / the）を入れてください",
     "prompt":"She plays ___ piano every evening.",
     "answer":"the","alternatives":[],
     "hint":"楽器名の前に使う冠詞は？",
     "explanation":"楽器名の前には定冠詞 'the' を使います（play the piano / guitar / violin）。",
     "error_tags":["article"]},

    {"id":"a2-art-03","level":"a2","category":"冠詞",
     "instruction":"空欄に a / an / the / -（なし）を入れてください",
     "prompt":"___ honesty is the best policy.",
     "answer":"-","alternatives":[""],
     "hint":"抽象的な概念・一般論の前に冠詞は必要？",
     "explanation":"抽象名詞（honesty, love, freedom）を一般的に指す場合、冠詞は不要です。",
     "error_tags":["article"]},

    # 前置詞
    {"id":"a2-prep-01a","level":"a2","category":"前置詞",
     "instruction":"空欄に at / in / on のどれかを入れてください",
     "prompt":"The meeting is ___ Monday.",
     "answer":"on","alternatives":[],
     "hint":"曜日の前に使う前置詞は？",
     "explanation":"曜日の前には on を使います（on Monday, on Friday）。月→ in, 時刻→ at。",
     "error_tags":["preposition","time_expressions"]},

    {"id":"a2-prep-01b","level":"a2","category":"前置詞",
     "instruction":"空欄に at / in / on のどれかを入れてください",
     "prompt":"The meeting starts ___ 3 p.m.",
     "answer":"at","alternatives":[],
     "hint":"時刻の前に使う前置詞は？",
     "explanation":"時刻の前には at を使います（at 3 p.m., at noon, at midnight）。",
     "error_tags":["preposition","time_expressions"]},

    {"id":"a2-prep-02","level":"a2","category":"前置詞",
     "instruction":"日本語の意味に合う前置詞を選んでください",
     "prompt":"I'm not very good ___ cooking.（料理が得意ではない）",
     "answer":"at","alternatives":[],
     "hint":"be good ___ は定番コロケーション",
     "explanation":"be good at ～「〜が得意」は必須コロケーション。bad at / skilled at も同じ形。",
     "error_tags":["preposition","collocation"]},

    # 基本動詞の使い分け
    {"id":"a2-verb-01","level":"a2","category":"動詞の使い分け",
     "instruction":"make か do を選んでください",
     "prompt":"Can you ___ me a favour?（お願いがあるのですが）",
     "answer":"do","alternatives":[],
     "hint":"do a favour / make a mistake — どちらが正しい？",
     "explanation":"do a favour（お願いを聞く）, make a mistake（ミスをする）は固定表現。"
         "\ndo = 行為・活動, make = 創造・製造 が大まかな区別です。",
     "error_tags":["collocation","make_do"]},

    {"id":"a2-verb-02","level":"a2","category":"動詞の使い分け",
     "instruction":"say か tell を選んでください",
     "prompt":"She ___ me that she was tired.",
     "answer":"told","alternatives":["told me"],
     "hint":"say と tell の後に来るものの違いは？",
     "explanation":"tell は必ず「誰に」（told me）が続きます。say の後には直接目的語（人）は来ません（said that...）。",
     "error_tags":["say_tell","verb_usage"]},

    # ════════════════════════════════════════
    # B1 — 実用文法・フレーズ動詞・熟語
    # ════════════════════════════════════════

    # 時制
    {"id":"b1-tense-01","level":"b1","category":"時制",
     "instruction":"括弧内の動詞を適切な形に変えてください",
     "prompt":"By the time she arrived, he (already / leave) ___.",
     "answer":"had already left","alternatives":["had left already"],
     "hint":"過去のある時点より前に完了した動作は？",
     "explanation":"by the time + 過去形 → 過去完了形 (had + p.p.) を使います。",
     "error_tags":["tense","past_perfect"]},

    {"id":"b1-tense-02","level":"b1","category":"時制",
     "instruction":"空欄に適切な時制の動詞を入れてください",
     "prompt":"I ___ (live) here since 2020.",
     "answer":"have lived","alternatives":["have been living"],
     "hint":"since は何時制と一緒に使う？",
     "explanation":"since（〜以来）は現在完了形と一緒に使います。継続を強調する場合は現在完了進行形も可。",
     "error_tags":["tense","present_perfect"]},

    # 前置詞
    {"id":"b1-prep-01","level":"b1","category":"前置詞",
     "instruction":"空欄に適切な前置詞を入れてください",
     "prompt":"She has been working ___ this company for five years.",
     "answer":"for","alternatives":[],
     "hint":"期間と起点、どちらを表す？",
     "explanation":"期間（five years）の前には for。起点（2019年から）には since を使います。",
     "error_tags":["preposition"]},

    {"id":"b1-prep-02","level":"b1","category":"前置詞",
     "instruction":"空欄に適切な前置詞を入れてください",
     "prompt":"He's very keen ___ photography.",
     "answer":"on","alternatives":[],
     "hint":"keen の後ろに来る前置詞は？",
     "explanation":"be keen on ～「〜に熱中している」。interested in, passionate about も同類の表現。",
     "error_tags":["preposition","collocation"]},

    # フレーズ動詞（Phrasal Verbs）★
    {"id":"b1-pv-01","level":"b1","category":"フレーズ動詞",
     "instruction":"日本語の意味に合うフレーズ動詞を選んでください",
     "prompt":"I need to ___ ___ this report before the deadline.（終わらせる）\n選択肢: finish up / give up / put off",
     "answer":"finish up","alternatives":["wrap up"],
     "hint":"「〜を完成させる・終わらせる」という意味のフレーズ動詞は？",
     "explanation":"finish up = 仕上げる・完成させる。give up = あきらめる, put off = 先延ばしにする。",
     "error_tags":["phrasal_verb"]},

    {"id":"b1-pv-02","level":"b1","category":"フレーズ動詞",
     "instruction":"下線部をフレーズ動詞に言い換えてください",
     "prompt":"Can you postpone the meeting until Friday?\n→ Can you ___ ___ the meeting until Friday?",
     "answer":"put off","alternatives":["push back"],
     "hint":"postpone の口語的なフレーズ動詞は？",
     "explanation":"put off = 先延ばしにする（postpone の口語版）。日常会話では put off の方が自然です。",
     "error_tags":["phrasal_verb","vocabulary"]},

    {"id":"b1-pv-03","level":"b1","category":"フレーズ動詞",
     "instruction":"空欄に適切な副詞/前置詞を入れてください",
     "prompt":"The event was called ___ due to bad weather.（中止になった）",
     "answer":"off","alternatives":[],
     "hint":"call off = ?",
     "explanation":"call off = 中止する・取りやめる。cancel より口語的でよく使われます。",
     "error_tags":["phrasal_verb"]},

    {"id":"b1-pv-04","level":"b1","category":"フレーズ動詞",
     "instruction":"空欄に適切な副詞/前置詞を入れてください",
     "prompt":"I ran ___ an old friend at the supermarket yesterday.（ばったり会った）",
     "answer":"into","alternatives":[],
     "hint":"run into = ?",
     "explanation":"run into = ばったり出会う（meet by chance）。run across も同じ意味。",
     "error_tags":["phrasal_verb"]},

    {"id":"b1-pv-05","level":"b1","category":"フレーズ動詞",
     "instruction":"空欄に適切な副詞/前置詞を入れてください",
     "prompt":"She looks ___ her children while working from home.（世話をする）",
     "answer":"after","alternatives":[],
     "hint":"look after = ?",
     "explanation":"look after = 世話をする（take care of）。look up to = 尊敬する, look forward to = 楽しみにする。",
     "error_tags":["phrasal_verb"]},

    # イディオム（Idioms）★
    {"id":"b1-id-01","level":"b1","category":"イディオム",
     "instruction":"空欄に適切な語を入れてイディオムを完成させてください",
     "prompt":"The exam was a piece of ___.（試験は朝飯前だった）",
     "answer":"cake","alternatives":[],
     "hint":"非常に簡単なことを食べ物に例えたイディオム",
     "explanation":"a piece of cake = とても簡単なこと。'It's a piece of cake!' は日常会話で頻出。",
     "error_tags":["idiom"]},

    {"id":"b1-id-02","level":"b1","category":"イディオム",
     "instruction":"空欄に適切な語を入れてイディオムを完成させてください",
     "prompt":"Let's call it a ___ for today.（今日はここまでにしましょう）",
     "answer":"day","alternatives":[],
     "hint":"仕事・活動を終わりにするときの定番表現",
     "explanation":"call it a day = 今日の作業を終わりにする。ミーティングや仕事の終わりに使います。",
     "error_tags":["idiom"]},

    {"id":"b1-id-03","level":"b1","category":"イディオム",
     "instruction":"イディオムの意味を選んでください",
     "prompt":"'Break a leg!' の意味は？\na) 足を骨折して  b) 頑張って・うまくいくように  c) 急いで",
     "answer":"b","alternatives":["b) 頑張って・うまくいくように","頑張って"],
     "hint":"演劇界で使われてきた言葉",
     "explanation":"Break a leg! = 頑張って！（Good luck!の代わりに使う）。舞台俳優に「Good luck」と言うのは縁起が悪いとされたため。",
     "error_tags":["idiom"]},

    {"id":"b1-id-04","level":"b1","category":"イディオム",
     "instruction":"空欄に適切な語を入れてイディオムを完成させてください",
     "prompt":"I'm feeling ___ the weather today.（今日は体調が優れない）",
     "answer":"under","alternatives":[],
     "hint":"under the ___",
     "explanation":"under the weather = 体調が優れない（not feeling well）。軽い不調を表すカジュアルな表現。",
     "error_tags":["idiom"]},

    {"id":"b1-id-05","level":"b1","category":"イディオム",
     "instruction":"空欄に適切な語を入れてイディオムを完成させてください",
     "prompt":"We're in the same ___.（私たちは同じ状況にいる）",
     "answer":"boat","alternatives":[],
     "hint":"同じ乗り物に乗っているイメージ",
     "explanation":"be in the same boat = 同じ状況・立場にいる。\"We're all in this together\" に近い意味。",
     "error_tags":["idiom"]},

    # 仮定法
    {"id":"b1-cond-01","level":"b1","category":"仮定法",
     "instruction":"仮定法過去の文を完成させてください",
     "prompt":"If I ___ (have) more time, I would travel the world.",
     "answer":"had","alternatives":[],
     "hint":"仮定法過去の if 節では動詞は過去形",
     "explanation":"仮定法過去: If + 主語 + 動詞の過去形（be動詞は were）→ would + 原形。",
     "error_tags":["conditional","subjunctive"]},

    # コロケーション（Collocations）★
    {"id":"b1-col-01","level":"b1","category":"コロケーション",
     "instruction":"自然なコロケーションを選んでください",
     "prompt":"She ___ a speech at the conference.\n選択肢: did / made / had / took",
     "answer":"made","alternatives":[],
     "hint":"スピーチを「する」は make？ do？",
     "explanation":"make a speech（スピーチをする）は固定コロケーション。"
         "\nmake: a decision, a mistake, a difference, an effort, a suggestion",
     "error_tags":["collocation","make_do"]},

    {"id":"b1-col-02","level":"b1","category":"コロケーション",
     "instruction":"自然なコロケーションを選んでください",
     "prompt":"He ___ his driving test on the third attempt.\n選択肢: passed / won / succeeded / achieved",
     "answer":"passed","alternatives":[],
     "hint":"試験に「合格する」の動詞は？",
     "explanation":"pass an exam/test（試験に合格する）が正しいコロケーション。"
         "\n'win a test' とは言いません。反対は fail an exam。",
     "error_tags":["collocation"]},

    {"id":"b1-col-03","level":"b1","category":"コロケーション",
     "instruction":"空欄に適切な動詞を入れてください",
     "prompt":"Let's ___ a break. I'm exhausted.（休憩しよう）",
     "answer":"take","alternatives":[],
     "hint":"take / have / do のどれ？",
     "explanation":"take a break が最も一般的。have a break（英）も可。"
         "\ntake: a break, a photo, a risk, a shower, a seat",
     "error_tags":["collocation"]},

    # ════════════════════════════════════════
    # B2 — 中上級文法・表現
    # ════════════════════════════════════════

    # 受動態
    {"id":"b2-pass-01","level":"b2","category":"受動態",
     "instruction":"能動態を受動態に書き換えてください",
     "prompt":"Scientists have discovered a new species of bird.",
     "answer":"A new species of bird has been discovered by scientists.",
     "alternatives":["A new species of bird has been discovered."],
     "hint":"現在完了の受動態: has/have + been + 過去分詞",
     "explanation":"能動態の目的語が主語になり、動詞は 'has been + p.p.' の形。by 句は省略可能。",
     "error_tags":["passive","tense"]},

    # 関係詞
    {"id":"b2-rel-01","level":"b2","category":"関係詞",
     "instruction":"適切な関係代名詞を選んでください: who / which / whose",
     "prompt":"The report, ___ findings were surprising, was published last week.",
     "answer":"whose","alternatives":[],
     "hint":"report の「所有」を表す関係代名詞は？",
     "explanation":"whose は所有を表します。'whose findings' = 'the report's findings'。",
     "error_tags":["relative_clause"]},

    {"id":"b2-rel-02","level":"b2","category":"関係詞",
     "instruction":"空欄に適切な語を入れてください（省略可能な場合は省略形も可）",
     "prompt":"That's the restaurant ___ we had our first date.",
     "answer":"where","alternatives":["in which","that"],
     "hint":"場所を表す関係副詞は？",
     "explanation":"場所を表す関係副詞 where（= in which）。"
         "\n'the place where / that' は目的格なら省略も可。",
     "error_tags":["relative_clause"]},

    # フレーズ動詞（上級）★
    {"id":"b2-pv-01","level":"b2","category":"フレーズ動詞",
     "instruction":"空欄に適切な副詞/前置詞を入れてください",
     "prompt":"I didn't expect him to turn ___ the job offer.（断るとは思わなかった）",
     "answer":"down","alternatives":[],
     "hint":"turn down = ?",
     "explanation":"turn down = 断る・拒否する（decline/reject の口語版）。音量を下げるという意味もあります。",
     "error_tags":["phrasal_verb"]},

    {"id":"b2-pv-02","level":"b2","category":"フレーズ動詞",
     "instruction":"空欄に適切な副詞/前置詞を入れてください",
     "prompt":"The company is trying to cut ___ on expenses.（経費を削減しようとしている）",
     "answer":"down","alternatives":[],
     "hint":"cut down on = ?",
     "explanation":"cut down on = 〜を削減する・減らす。cut back on も同義。",
     "error_tags":["phrasal_verb"]},

    {"id":"b2-pv-03","level":"b2","category":"フレーズ動詞",
     "instruction":"空欄に適切な副詞/前置詞を入れてください",
     "prompt":"She came ___ an interesting article while browsing the internet.（偶然見つけた）",
     "answer":"across","alternatives":[],
     "hint":"come across = ?",
     "explanation":"come across = 偶然出会う・見つける（stumble upon）。"
         "\n'He came across as very confident.'（〜という印象を与えた）という意味もあります。",
     "error_tags":["phrasal_verb"]},

    # イディオム（上級）★
    {"id":"b2-id-01","level":"b2","category":"イディオム",
     "instruction":"空欄に適切な語を入れてイディオムを完成させてください",
     "prompt":"Let's not beat around the ___.（遠回しに言うのはやめよう）",
     "answer":"bush","alternatives":[],
     "hint":"around the ___",
     "explanation":"beat around the bush = 遠回しに言う・核心を避ける。"
         "\n直接的に話してほしいときに 'Stop beating around the bush!' と言います。",
     "error_tags":["idiom"]},

    {"id":"b2-id-02","level":"b2","category":"イディオム",
     "instruction":"空欄に適切な語を入れてイディオムを完成させてください",
     "prompt":"That meeting was just the tip of the ___.（あの会議は問題のほんの一部に過ぎなかった）",
     "answer":"iceberg","alternatives":[],
     "hint":"水面下に大部分が隠れているものは？",
     "explanation":"tip of the iceberg = 氷山の一角。問題の表面しか見えていないことを表します。",
     "error_tags":["idiom"]},

    {"id":"b2-id-03","level":"b2","category":"イディオム",
     "instruction":"空欄に適切な語を入れてイディオムを完成させてください",
     "prompt":"She hit the ___ when she heard the news.（そのニュースを聞いて彼女は激怒した）",
     "answer":"roof","alternatives":["ceiling"],
     "hint":"怒りが天井を突き破るイメージ",
     "explanation":"hit the roof / hit the ceiling = 激怒する（go ballistic とも言う）。",
     "error_tags":["idiom"]},

    {"id":"b2-id-04","level":"b2","category":"イディオム",
     "instruction":"このイディオムの意味として最も近いものを選んでください",
     "prompt":"'It's raining cats and dogs outside.'\na) 猫と犬が外にいる  b) 土砂降りだ  c) 外は嵐だ",
     "answer":"b","alternatives":["b) 土砂降りだ","土砂降り"],
     "hint":"大雨を表す有名なイディオム",
     "explanation":"It's raining cats and dogs = 土砂降りだ（pouring heavily）。由来には諸説あります。",
     "error_tags":["idiom"]},

    # コロケーション（B2）★
    {"id":"b2-col-01","level":"b2","category":"コロケーション",
     "instruction":"空欄に最もよく使われる形容詞を入れてください",
     "prompt":"He made a ___ decision to quit his job.（思い切った決断）\n選択肢: bold / strong / big / heavy",
     "answer":"bold","alternatives":["brave","difficult"],
     "hint":"「大胆な・思い切った」決断は？",
     "explanation":"bold decision（大胆な決断）が自然なコロケーション。"
         "\nmake a bold move, bold statement なども頻出。",
     "error_tags":["collocation","adjective_noun"]},

    {"id":"b2-col-02","level":"b2","category":"コロケーション",
     "instruction":"空欄に適切な動詞を入れてください",
     "prompt":"The new policy will ___ a significant impact on the economy.",
     "answer":"have","alternatives":["make"],
     "hint":"impact の前に来る動詞は have？ make？",
     "explanation":"have an impact on ～（〜に影響を与える）が標準的なコロケーション。"
         "\nmake an impact も可。ただし 'do an impact' とは言いません。",
     "error_tags":["collocation"]},

    # 比較表現
    {"id":"b2-comp-01","level":"b2","category":"比較",
     "instruction":"「〜すればするほど上手くなる」の構文を完成させてください。最初の空欄に入る語は？",
     "prompt":"The ___ you practice, the better you'll get.",
     "answer":"more","alternatives":[],
     "hint":"The + 比較級 ～, the + 比較級 ～ の構文。最初の空欄は？",
     "explanation":"The + 比較級 ～, the + 比較級 ～ の構文です。\n'The more you practice, the better you'll get.'（練習すればするほど上手くなる）",
     "error_tags":["comparison","grammar_structure"]},

    {"id":"b2-comp-02","level":"b2","category":"比較",
     "instruction":"ことわざを完成させてください。空欄に入る語は？",
     "prompt":"The sooner, the ___.（早ければ早いほどよい）",
     "answer":"better","alternatives":[],
     "hint":"The + 比較級, the + 比較級 の構文",
     "explanation":"'The sooner, the better.' は頻出のことわざ。\n同じ構文: 'The bigger, the better.' / 'The more, the merrier.'",
     "error_tags":["comparison","grammar_structure"]},

    # ビジネス英語★
    {"id":"b2-biz-01","level":"b2","category":"ビジネス英語",
     "instruction":"ビジネスメールで使う丁寧な表現に言い換えてください",
     "prompt":"I want to know when the report will be ready.\n→ I was ___ if you could let me know when the report will be ready.",
     "answer":"wondering","alternatives":[],
     "hint":"I was wondering if... は依頼の定番フレーズ",
     "explanation":"I was wondering if... = 〜していただけますか（非常に丁寧な依頼）。"
         "\nCould you...? より遠回しで、ビジネスメールで広く使われます。",
     "error_tags":["business_english","politeness"]},

    {"id":"b2-biz-02","level":"b2","category":"ビジネス英語",
     "instruction":"空欄に適切な前置詞を入れてください",
     "prompt":"Please find the report attached ___ reference.（ご参考のために添付いたします）",
     "answer":"for","alternatives":[],
     "hint":"for your reference = ご参考のために",
     "explanation":"'Please find ... attached for your reference.' はビジネスメールの定番表現。"
         "\nPlease find attached the report. も頻出（attached の位置に注意）。",
     "error_tags":["business_english","preposition"]},

    # ════════════════════════════════════════
    # C1 — 上級文法・洗練された表現
    # ════════════════════════════════════════

    # 倒置
    {"id":"c1-inver-01","level":"c1","category":"倒置",
     "instruction":"否定副詞を文頭に出す倒置形に書き換えてください",
     "prompt":"I have never seen such a beautiful sunset.",
     "answer":"Never have I seen such a beautiful sunset.",
     "alternatives":[],
     "hint":"否定の副詞を文頭に出すと主語と助動詞が入れ替わります",
     "explanation":"否定副詞（Never, Seldom, Rarely）を文頭に出すと倒置が起きます。フォーマルな文体。",
     "error_tags":["inversion","emphasis"]},

    {"id":"c1-inver-02","level":"c1","category":"倒置",
     "instruction":"倒置を用いて書き換えてください",
     "prompt":"She had no sooner left than it started to rain.",
     "answer":"No sooner had she left than it started to rain.",
     "alternatives":[],
     "hint":"No sooner ... than の倒置形",
     "explanation":"'No sooner had + S + p.p. than ...' = 〜するとすぐに…（= As soon as）。"
         "\n'Hardly had she left when ...' も同義。",
     "error_tags":["inversion","tense"]},

    # 名詞化
    {"id":"c1-nomm-01","level":"c1","category":"名詞化",
     "instruction":"下線部を名詞化して書き換えてください",
     "prompt":"It is important that we decide quickly.\n→ The quick ___ is important.",
     "answer":"decision","alternatives":[],
     "hint":"'decide' の名詞形は？",
     "explanation":"decide → decision。名詞化（nominalisation）は学術・ビジネス英語で頻繁に使われます。",
     "error_tags":["nominalisation","vocabulary"]},

    # 洗練されたイディオム★
    {"id":"c1-id-01","level":"c1","category":"イディオム",
     "instruction":"空欄に適切な語を入れてください",
     "prompt":"The new manager really hit the ground ___ and achieved results quickly.（すぐに成果を出した）",
     "answer":"running","alternatives":[],
     "hint":"hit the ground ___",
     "explanation":"hit the ground running = 最初からフルスピードで動き出す。"
         "\nビジネス文脈で特によく使われます。",
     "error_tags":["idiom","business_english"]},

    {"id":"c1-id-02","level":"c1","category":"イディオム",
     "instruction":"空欄に適切な語を入れてください",
     "prompt":"She's been burning the midnight ___ to finish the thesis.（論文を仕上げるために夜遅くまで働いている）",
     "answer":"oil","alternatives":[],
     "hint":"burn the midnight ___",
     "explanation":"burn the midnight oil = 深夜まで働く・勉強する。"
         "\n電灯がなかった時代にランプで夜遅くまで作業したことから。",
     "error_tags":["idiom"]},

    {"id":"c1-id-03","level":"c1","category":"イディオム",
     "instruction":"空欄に適切な語を入れてください",
     "prompt":"The project is back on ___ after the funding issue was resolved.（資金問題が解決してプロジェクトが軌道に戻った）",
     "answer":"track","alternatives":[],
     "hint":"on track = ?",
     "explanation":"back on track = 元の軌道に戻る・立て直す。"
         "\nget back on track, stay on track も頻出表現。",
     "error_tags":["idiom","business_english"]},

    # フォーマル表現・言い換え
    {"id":"c1-formal-01","level":"c1","category":"フォーマル表現",
     "instruction":"カジュアルな表現をフォーマルに言い換えてください",
     "prompt":"We need to look into this problem. (フォーマルに)\n→ We need to ___ this matter.",
     "answer":"investigate","alternatives":["examine","scrutinize","look into"],
     "hint":"look into のフォーマル版は？",
     "explanation":"look into（調査する）のフォーマル版は investigate / examine。"
         "\nフォーマル文書では動詞1語を好みます（phrasal verb を避ける傾向）。",
     "error_tags":["formality","vocabulary","phrasal_verb"]},

    {"id":"c1-formal-02","level":"c1","category":"フォーマル表現",
     "instruction":"空欄に適切な表現を入れてください",
     "prompt":"___ to your email of 5th April, I am writing to confirm our meeting.（4月5日のメールへの返信として）",
     "answer":"With reference","alternatives":["In response","Further","Regarding"],
     "hint":"ビジネスメールの冒頭でよく使うフレーズ",
     "explanation":"With reference to ... = 〜に関して（正式なメールの書き出し）。"
         "\n'Further to your email' も同様の意味でよく使われます。",
     "error_tags":["business_english","formality"]},

    # 接続表現・ディスコースマーカー
    {"id":"c1-disc-01","level":"c1","category":"接続表現",
     "instruction":"文脈に合う接続表現を選んでください",
     "prompt":"The data shows a clear trend. ___, we must be cautious about drawing conclusions.\n選択肢: Moreover / Nevertheless / Therefore / In addition",
     "answer":"Nevertheless","alternatives":["However"],
     "hint":"前の文と逆接の関係にある接続詞は？",
     "explanation":"Nevertheless = それにもかかわらず（however のよりフォーマルな版）。"
         "\nMoreover/In addition = さらに, Therefore = したがって（逆接ではない）。",
     "error_tags":["discourse_marker","conjunction"]},

    # ════════════════════════════════════════
    # C2 — 最上級・ネイティブ的表現
    # ════════════════════════════════════════

    # 仮定法
    {"id":"c2-subj-01","level":"c2","category":"仮定法",
     "instruction":"仮定法過去完了を使って一文で書き換えてください",
     "prompt":"I didn't study hard, so I didn't pass the exam.",
     "answer":"If I had studied hard, I would have passed the exam.",
     "alternatives":["Had I studied hard, I would have passed the exam."],
     "hint":"If + 過去完了 → would have + p.p.",
     "explanation":"仮定法過去完了は過去の事実に反する仮定。倒置形（Had I...）はよりフォーマルです。",
     "error_tags":["subjunctive","conditional","past_perfect"]},

    # 省略・強調構文
    {"id":"c2-cleft-01","level":"c2","category":"強調構文",
     "instruction":"強調構文（It is ... that）を使って書き換えてください",
     "prompt":"John broke the window yesterday. (John を強調する)\n→ It was ___ that broke the window yesterday.",
     "answer":"John","alternatives":[],
     "hint":"It is/was + 強調したい要素 + that ...",
     "explanation":"強調構文 'It is/was X that ...' で特定の要素を強調。"
         "\n'It was yesterday that ...' なら時間の強調、'It was the window that ...' なら目的語の強調。",
     "error_tags":["cleft_sentence","emphasis"]},

    # ニュアンスのある表現★
    {"id":"c2-nuance-01","level":"c2","category":"ニュアンス",
     "instruction":"2つの文の違いを説明し、空欄に正しい語を入れてください",
     "prompt":"A: 'I managed to finish the report.' → 苦労したが、なんとか___した\nB: 'I succeeded in finishing the report.' → 成功___した\n\n A の空欄: ?",
     "answer":"完了","alternatives":["終わった","できた"],
     "hint":"manage to と succeed in のニュアンスの違いは？",
     "explanation":"manage to = 苦労・困難を乗り越えてなんとか〜した（努力の過程を含意）。"
         "\nsucceed in = 成功した（結果のみ）。ニュアンスは大きく異なります。",
     "error_tags":["nuance","vocabulary"]},

    {"id":"c2-nuance-02","level":"c2","category":"ニュアンス",
     "instruction":"文脈に最も適した動詞を選んでください",
     "prompt":"The politician ___ the importance of education in his speech.\n選択肢: said / stated / asserted / proclaimed",
     "answer":"emphasized","alternatives":["stressed","highlighted","underscored"],
     "hint":"スピーチで「重要性を強調した」のに最適な動詞は？",
     "explanation":"emphasized / stressed / highlighted / underscored（強調した）が最適。"
         "\nsaid（言った）は中立的すぎ、asserted（主張した）は対立を含意、proclaimed（宣言した）は大げさ。",
     "error_tags":["nuance","vocabulary","register"]},

    # 慣用表現・ことわざ★
    {"id":"c2-prov-01","level":"c2","category":"ことわざ・慣用句",
     "instruction":"ことわざを完成させてください",
     "prompt":"'Don't judge a book by its ___.'（見た目で判断するな）",
     "answer":"cover","alternatives":[],
     "hint":"本の見た目で内容を判断しない",
     "explanation":"Don't judge a book by its cover. = 外見で判断するな。"
         "\n類義: Appearances can be deceiving.（見かけは当てにならない）",
     "error_tags":["proverb","idiom"]},

    {"id":"c2-prov-02","level":"c2","category":"ことわざ・慣用句",
     "instruction":"ことわざを完成させてください",
     "prompt":"'Actions speak louder than ___.'（行動は言葉よりも雄弁に語る）",
     "answer":"words","alternatives":[],
     "hint":"言葉より行動が大事という教え",
     "explanation":"Actions speak louder than words. = 言葉よりも行動が大切。"
         "\n約束や宣言よりも、実際の行動を見るべきという意味です。",
     "error_tags":["proverb","idiom"]},

    {"id":"c2-prov-03","level":"c2","category":"ことわざ・慣用句",
     "instruction":"空欄に適切な語を入れてください",
     "prompt":"You can't have your ___ and eat it too.（いいとこ取りはできない）",
     "answer":"cake","alternatives":[],
     "hint":"ケーキを持ちながら食べることはできない",
     "explanation":"You can't have your cake and eat it too. = 二兎を追う者は一兎をも得ず（どちらも欲しがることはできない）。",
     "error_tags":["proverb","idiom"]},

    # 文体・語彙レベル
    {"id":"c2-style-01","level":"c2","category":"語彙・文体",
     "instruction":"（　）内の語をより洗練された表現に言い換えてください",
     "prompt":"The results were (very surprising).\n→ The results were ___.",
     "answer":"astounding","alternatives":["astonishing","remarkable","striking","staggering"],
     "hint":"very surprising の一語版で、より印象的な語は？",
     "explanation":"very surprising → astonishing / astounding / staggering（驚異的な）。"
         "\n上級ライティングでは 'very + 形容詞' を強い形容詞一語に置き換えると洗練されます。",
     "error_tags":["vocabulary","style"]},

    {"id":"c2-style-02","level":"c2","category":"語彙・文体",
     "instruction":"空欄に最も適切な語を入れてください",
     "prompt":"His argument was so ___ that nobody could refute it.（論破できないほど説得力があった）\n選択肢: convincing / compelling / persuasive / irrefutable",
     "answer":"irrefutable","alternatives":["compelling","airtight"],
     "hint":"「論駁不可能な」という意味の語は？",
     "explanation":"irrefutable = 論駁不可能な・反論の余地がない（最も強い意味）。"
         "\ncompelling = 非常に説得力がある, persuasive = 説得力がある（程度が弱い順）。",
     "error_tags":["vocabulary","nuance"]},

]

WRITING_TOPICS = [
    "Describe your ideal weekend",
    "What technology has changed your life the most?",
    "Should remote work become the default working style?",
    "Write about a person who has inspired you",
    "What would you do if you had unlimited free time?",
    "Describe a challenge you've overcome",
    "Is social media more harmful than helpful?",
    "What skill do you most want to learn and why?",
    "Write about a place that is special to you",
    "How do you think AI will change education in the future?",
    "What is the most important quality in a friendship?",
    "Describe your dream career and why it appeals to you",
]


# ── Claude API ─────────────────────────────────────────────────────────────────

def get_api_key(db: Database) -> Optional[str]:
    return db.get_config("api_key") or os.environ.get("ANTHROPIC_API_KEY")


def claude_review_writing(api_key: str, topic: str, text: str) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""あなたは日本人向けの英語教師です。以下の英作文を詳しく添削してください。

お題: {topic}
学習者の英作文:
---
{text}
---

以下のJSON形式のみで回答してください（マークダウンのコードブロック不要）:
{{
  "corrected": "完全に添削した英文",
  "grammar_score": 0から10の数値,
  "naturalness_score": 0から10の数値,
  "vocabulary_score": 0から10の数値,
  "overall_comment": "全体的なコメント（日本語、100文字程度）",
  "grammar_errors": [
    {{
      "original": "誤りのある箇所",
      "corrected": "正しい表現",
      "explanation_ja": "なぜ間違いか（日本語で簡潔に）",
      "error_type": "tense/article/preposition/word_order/spelling/other のいずれか"
    }}
  ],
  "good_points": ["よかった点（日本語）"],
  "suggestions": ["改善提案（日本語）"],
  "error_tags": ["出現したエラータイプのリスト"]
}}"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    text_response = message.content[0].text.strip()
    text_response = text_response.strip("```json").strip("```").strip()
    return json.loads(text_response)


def claude_explain_error(api_key: str, prompt_text: str, user_answer: str, correct: str, hint: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[{"role": "user", "content":
            f"日本人英語学習者への短い説明（100文字以内の日本語）:\n"
            f"問題: {prompt_text}\n学習者の回答: {user_answer}\n正解: {correct}\nヒント: {hint}\n"
            f"なぜ間違いで、なぜ正解がそうなるかを簡潔に説明してください。"
        }]
    )
    return message.content[0].text.strip()


# ── ユーティリティ ──────────────────────────────────────────────────────────────

def levenshtein(a: str, b: str) -> int:
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        new_dp = [i] + [0] * n
        for j in range(1, n + 1):
            if a[i-1] == b[j-1]:
                new_dp[j] = dp[j-1]
            else:
                new_dp[j] = 1 + min(dp[j], new_dp[j-1], dp[j-1])
        dp = new_dp
    return dp[n]


def check_answer(exercise: dict, user_input: str) -> tuple[str, Optional[str], Optional[str]]:
    """Returns ('correct'|'tense'|'wrong', correct_answer, hint_message)"""
    user = user_input.strip().lower().replace("'", "\'")
    correct = exercise["answer"].strip().lower().replace("'", "\'")
    alts = [a.strip().lower().replace("'", "\'") for a in exercise.get("alternatives", [])]

    if user == correct or user in alts:
        return "correct", None, None

    def stem_match(a: str, b: str) -> bool:
        min_len = min(len(a), len(b), 4)
        return min_len >= 3 and (a[:min_len] == b[:min_len])

    user_words = user.split()
    correct_words = correct.split()
    if len(user_words) == len(correct_words):
        mismatches = [
            (uw, cw) for uw, cw in zip(user_words, correct_words)
            if uw != cw
        ]
        if mismatches and all(
            stem_match(uw, cw) or levenshtein(uw, cw) <= 2
            for uw, cw in mismatches
        ):
            mismatch_hints = []
            for uw, cw in mismatches:
                if uw != cw:
                    mismatch_hints.append(f"[yellow]{uw}[/yellow] → [green]{cw}[/green]")
            hint_msg = "活用・時制を確認:  " + "  ".join(mismatch_hints)
            return "tense", exercise["answer"], hint_msg

    return "wrong", exercise["answer"], None


def score_bar(score: float) -> Text:
    filled = round(score)
    bar = "█" * filled + "░" * (10 - filled)
    color = "green" if score >= 8 else "yellow" if score >= 6 else "red"
    return Text(bar, style=color)


def read_multiline() -> str:
    """空行2回で入力終了"""
    console.print("[dim](入力が終わったら空行を2回押してください)[/dim]")
    lines = []
    empty_count = 0
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            empty_count += 1
            if empty_count >= 2:
                break
            lines.append(line)
        else:
            empty_count = 0
            lines.append(line)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


# ── CLI コマンド ────────────────────────────────────────────────────────────────

@click.group()
def cli():
    """🎓 english-cli — CLI英語学習ツール（AI添削・SRS・文法ドリル）"""
    pass


@cli.command()
def study():
    """今日の学習を開始する（メニュー形式）"""
    db = Database()
    stats = db.get_stats()

    console.print(Panel(
        f"[bold cyan]🎓 english-cli[/bold cyan]\n"
        f"SRS復習待ち: [yellow]{stats['due_today']}単語[/yellow]  |  "
        f"累計セッション: [cyan]{stats['total_sessions']}[/cyan]  |  "
        f"習得単語: [green]{stats['vocab_learned']}[/green]",
        title="今日の学習",
        border_style="cyan"
    ))

    while True:
        console.print("\n[bold]学習メニュー[/bold]")
        console.print("  [bright_green][1][/bright_green] 文法ドリル")
        console.print("  [bright_green][2][/bright_green] 自由英作文（Claude 添削）")
        console.print("  [bright_green][3][/bright_green] 単語 SRS レビュー")
        console.print("  [dim][q][/dim] 終了")

        choice = Prompt.ask("\n選択").strip()

        if choice == "1":
            level = Prompt.ask("レベル (a2/b1/b2/c1/c2)", default="b1")
            _run_grammar(db, level)
        elif choice == "2":
            _run_write(db, None)
        elif choice == "3":
            _run_review(db)
        elif choice.lower() in ("q", "quit", "exit"):
            break
        else:
            console.print("[red]1, 2, 3 または q を入力してください[/red]")

    console.print("\n[bold green]お疲れ様でした！[/bold green] 🎉")


@cli.command()
@click.option("--topic", "-t", default=None, help="作文のお題（省略するとランダム）")
def write(topic):
    """自由英作文モード — Claude が添削します"""
    db = Database()
    _run_write(db, topic)


@cli.command()
@click.option("--level", "-l", default="b1", help="レベル: a2/b1/b2/c1/c2")
@click.option("--count", "-n", default=5, help="問題数")
def grammar(level, count):
    """文法ドリルを解く"""
    db = Database()
    _run_grammar(db, level, count)


@cli.command()
def review():
    """単語 SRS レビュー"""
    db = Database()
    _run_review(db)


@cli.command()
def stats():
    """学習統計を表示"""
    db = Database()
    s = db.get_stats()

    console.print()
    console.print(Panel("[bold]📈 学習統計[/bold]", border_style="cyan"))

    table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column(style="bright_white")
    table.add_row("総セッション数", str(s["total_sessions"]))
    table.add_row("平均スコア", f"{s['avg_score']:.1f}%")
    table.add_row("英作文提出数", str(s["total_writing"]))
    table.add_row("習得済み単語数", str(s["vocab_learned"]))
    table.add_row("今日のSRS復習数", f"[yellow]{s['due_today']}[/yellow]")
    console.print(table)

    if s["top_errors"]:
        console.print("\n[bold red]🔴 弱点パターン TOP5[/bold red]")
        for i, (tag, count) in enumerate(s["top_errors"], 1):
            bar = "▮" * min(count, 25)
            console.print(f"  {i}. [yellow]{tag:<20}[/yellow] [red]{bar}[/red] ({count}回)")

    console.print()


@cli.command()
@click.option("--api-key", "-k", default=None, help="Anthropic API キー")
def config(api_key):
    """設定（API キーなど）"""
    db = Database()

    if api_key:
        db.set_config("api_key", api_key)
        console.print("[green]✅ APIキーを保存しました。[/green]")
        console.print("これで Claude による添削・文法説明が使えます。")
    else:
        current = db.get_config("api_key")
        console.print(Panel("[bold]⚙️  設定[/bold]", border_style="dim"))
        if current:
            masked = current[:8] + "…" + current[-4:]
            console.print(f"  現在のAPIキー: [dim]{masked}[/dim]")
        else:
            console.print("  APIキー: [red]未設定[/red]\n")
            console.print("  設定するには:")
            console.print("    [bright_white]english-cli config --api-key sk-ant-...[/bright_white]")
            console.print("  または環境変数:")
            console.print("    [bright_white]export ANTHROPIC_API_KEY=sk-ant-...[/bright_white]")


# ── 内部実装 ────────────────────────────────────────────────────────────────────

def _run_grammar(db: Database, level: str, count: int = 5):
    exercises = [e for e in EXERCISES if e["level"] == level]
    if not exercises:
        console.print(f"[yellow]レベル '{level}' の問題が見つかりませんでした。[/yellow]")
        return

    random.shuffle(exercises)
    exercises = exercises[:count]
    api_key = get_api_key(db)

    console.print()
    console.print(Rule(f"[bold cyan]📝 文法ドリル — レベル {level.upper()}[/bold cyan]"))

    correct_count = 0
    start = time.time()
    error_tags = []

    for i, ex in enumerate(exercises, 1):
        console.print(f"\n[bold cyan]問題 {i}/{len(exercises)}[/bold cyan]  [dim]({ex['category']})[/dim]")
        console.print(f"\n  [white]{ex['instruction']}[/white]\n")
        console.print(f"  [bold yellow]{ex['prompt']}[/bold yellow]\n")

        hint_shown = False
        answered = False

        while not answered:
            user_input = Prompt.ask("  回答 [dim](hint/skip)[/dim]").strip()

            if user_input.lower() in ("hint", "h"):
                if not hint_shown:
                    console.print(f"  [bright_blue]💡 {ex['hint']}[/bright_blue]")
                    hint_shown = True
                else:
                    console.print(f"  [green]✅ 正解: {ex['answer']}[/green]")
                continue

            if user_input.lower() in ("skip", "s"):
                console.print(f"  スキップ — 正解: [green]{ex['answer']}[/green]")
                console.print(f"  [dim]{ex['explanation']}[/dim]")
                error_tags.extend(ex["error_tags"])
                for tag in ex["error_tags"]:
                    db.record_error(tag)
                answered = True
                continue

            result, correct_ans, hint_msg = check_answer(ex, user_input)

            if result == "correct":
                console.print("  [green]✅ 正解！[/green]")
                console.print(f"  [dim]{ex['explanation']}[/dim]")
                correct_count += 1
                answered = True

            elif result == "tense":
                console.print(f"  [yellow]⚠  惜しい！{hint_msg}[/yellow]")
                console.print(f"  正解: [green]{correct_ans}[/green]")
                console.print(f"  [dim]{ex['explanation']}[/dim]")
                error_tags.extend(ex["error_tags"])
                for tag in ex["error_tags"]:
                    db.record_error(tag)
                answered = True

            else:
                console.print("  [red]❌ もう一度試してみましょう[/red]  [dim](hint / skip)[/dim]")
                # LLM説明
                if api_key and not hint_shown:
                    try:
                        explanation = claude_explain_error(
                            api_key, ex["prompt"], user_input, ex["answer"], ex["hint"]
                        )
                        console.print(f"  [bright_blue]🤖 {explanation}[/bright_blue]")
                    except Exception:
                        pass

    duration = int(time.time() - start)
    score = correct_count / len(exercises) * 100
    score_color = "green" if score >= 80 else "yellow" if score >= 60 else "red"

    console.print()
    console.print(Rule("[dim]結果[/dim]"))
    console.print(f"  スコア: [bold]{correct_count}/{len(exercises)}[/bold]  [{score_color}]{score:.0f}%[/{score_color}]  [dim]({duration}秒)[/dim]")

    db.save_session(f"grammar-{level}", score, duration, error_tags)


def _run_write(db: Database, topic: Optional[str]):
    if not topic:
        topic = random.choice(WRITING_TOPICS)

    console.print()
    console.print(Rule("[bold cyan]✍️  自由英作文モード[/bold cyan]"))
    console.print(f"\n  お題: [bold yellow]{topic}[/bold yellow]\n")
    console.print("  英語で自由に書いてください（3〜8文程度がおすすめ）\n")

    text = read_multiline()

    if not text.strip():
        console.print("[red]テキストが入力されていません。[/red]")
        return

    api_key = get_api_key(db)
    if not api_key:
        console.print()
        console.print("[yellow]⚠  APIキーが設定されていません。[/yellow]")
        console.print("設定方法:")
        console.print("  [bright_white]english-cli config --api-key YOUR_KEY[/bright_white]")
        console.print("  または [bright_white]export ANTHROPIC_API_KEY=YOUR_KEY[/bright_white]")
        console.print("\n入力した英文:")
        console.print(Panel(text, border_style="dim"))
        return

    console.print("\n[bright_blue]  Claude が添削しています...[/bright_blue]")

    try:
        feedback = claude_review_writing(api_key, topic, text)
    except Exception as e:
        console.print(f"[red]❌ 添削エラー: {e}[/red]")
        return

    avg = (feedback.get("grammar_score", 0) + feedback.get("naturalness_score", 0) + feedback.get("vocabulary_score", 0)) / 3

    console.print()
    console.print(Rule("[bold]📊 採点結果[/bold]"))

    # スコア表示
    for label, key in [("文法正確性", "grammar_score"), ("自然さ・流暢さ", "naturalness_score"), ("語彙の豊富さ", "vocabulary_score")]:
        score = feedback.get(key, 0)
        bar = score_bar(score)
        score_color = "green" if score >= 8 else "yellow" if score >= 6 else "red"
        console.print(f"  {label:<16} {bar} [{score_color}]{score:.1f}[/{score_color}]/10")

    console.print(f"  {'総合スコア':<16} {score_bar(avg)} [bold]{avg:.1f}[/bold]/10")

    # 添削文
    console.print()
    console.print(Rule("[green]✅ 添削後の英文[/green]"))
    console.print(Panel(feedback.get("corrected", ""), border_style="green"))

    # 文法エラー
    errors = feedback.get("grammar_errors", [])
    if errors:
        console.print(Rule("[red]🔍 文法エラー詳細[/red]"))
        for err in errors:
            console.print(f"  [red strike]{err.get('original', '')}[/red strike] → [green]{err.get('corrected', '')}[/green]")
            console.print(f"  [dim]{err.get('explanation_ja', '')}[/dim]")
            console.print(f"  タグ: [yellow]{err.get('error_type', '')}[/yellow]\n")
            db.record_error(err.get("error_type", "other"))

    # よかった点
    good = feedback.get("good_points", [])
    if good:
        console.print(Rule("[green]🌟 よかった点[/green]"))
        for point in good:
            console.print(f"  • {point}")

    # 改善提案
    suggestions = feedback.get("suggestions", [])
    if suggestions:
        console.print()
        console.print(Rule("[blue]💡 改善提案[/blue]"))
        for s in suggestions:
            console.print(f"  • {s}")

    # 総合コメント
    console.print()
    console.print(Rule("[bold]💬 総合コメント[/bold]"))
    console.print(f"  {feedback.get('overall_comment', '')}")
    console.print()

    db.save_writing(topic, text, feedback)


def _run_review(db: Database):
    cards = db.get_due_cards(20)

    console.print()
    console.print(Rule("[bold cyan]🃏 単語 SRS レビュー[/bold cyan]"))

    if not cards:
        console.print("\n  [green]🎉 今日の復習はすべて完了しています！[/green]")
        return

    console.print(f"\n  復習する単語: [yellow]{len(cards)}枚[/yellow]\n")

    correct = 0
    for i, card in enumerate(cards, 1):
        console.print(f"[bold cyan]単語 {i}/{len(cards)}[/bold cyan]")
        console.print()
        console.print(f"  [bold yellow underline]{card['word']}[/bold yellow underline]")
        console.print()

        input("  意味を思い出したら Enter ↵ ")

        console.print(f"\n  意味: [bright_white]{card['meaning_ja']}[/bright_white]")
        console.print(f"  例文: [dim]{card['example']}[/dim]\n")

        console.print("  どれくらい覚えていましたか？")
        console.print("  [red][0][/red] 全く覚えていない   [red][1][/red] うっすら覚えていた")
        console.print("  [yellow][2][/yellow] 思い出せた（難しい）  [green][3][/green] 思い出せた  [green][4][/green] 簡単だった")

        while True:
            q = Prompt.ask("\n  評価 (0-4)")
            if q.strip() in ("0", "1", "2", "3", "4"):
                quality = int(q.strip())
                break
            console.print("  [red]0〜4 の数字を入力してください[/red]")

        if quality >= 3:
            correct += 1

        db.update_card_srs(card["id"], card["ease_factor"], card["interval_days"], quality)
        console.print()

    ratio = correct / len(cards) * 100
    console.print(Rule("[dim]完了[/dim]"))
    console.print(f"  {correct}/{len(cards)} 正解率 [{'green' if ratio >= 70 else 'yellow'}]{ratio:.0f}%[/{'green' if ratio >= 70 else 'yellow'}]")


# ── エントリーポイント ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
