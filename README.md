# english-cli 🎓

**rustlings 風 CLI 英語学習ツール** — AI添削・スペース反復・文法ドリルを一つのターミナルコマンドに。

```
════════════════════════════════════════════════════════════
  🎓 english-cli
════════════════════════════════════════════════════════════

学習メニュー:
  [1] 文法ドリル
  [2] 自由英作文（Claude 添削）
  [3] 単語 SRS レビュー
  [q] 終了
```

---

## 機能

### ✍️ 自由英作文 + Claude 添削
英文を書くと Claude が3軸で採点し、日本語で詳しくフィードバックします。

```
文法正確性     ████████░░  8.0/10
自然さ・流暢さ  ██████░░░░  6.5/10
語彙の豊富さ   ████████░░  7.5/10
総合スコア     ███████░░░  7.3/10

✅ 添削後の英文
Although I was tired, I decided to keep working on the project.

🔍 文法エラー詳細
Despite → Although
  「despite」の後には名詞句が来ます。節（SVO）が続く場合は「although」を使います。
  タグ: conjunction
```

### 📝 文法ドリル（A2〜C2）
レベル別の文法問題。間違えると Claude が日本語で解説。

```
問題 1/5  (仮定法)
  仮定法過去の文を完成させてください

  If I ___ (have) more time, I would travel the world.

  回答 > had
  ✅ 正解！
  仮定法過去: If + 主語 + 動詞の過去形（be動詞は were）
```

### 🃏 単語 SRS（SM-2アルゴリズム）
スペース反復で効率的に単語を定着。覚え具合（0〜4）を入力すると次回の復習日が自動計算されます。

```
単語 1/12
  ubiquitous

  意味: 至る所にある
  例文: Smartphones have become ubiquitous.

  [0] 全く覚えていない  [1] うっすら覚えていた
  [2] 思い出せた（難しい）  [3] 思い出せた  [4] 簡単だった
```

### 📈 弱点分析
間違えた文法タグを累積記録し、自分の弱点パターンを可視化します。

```
🔴 弱点パターン TOP5
  1. article              ▮▮▮▮▮▮▮▮▮▮ (10回)
  2. tense                ▮▮▮▮▮▮▮ (7回)
  3. preposition          ▮▮▮▮▮ (5回)
```

---

## インストール

```bash
git clone https://github.com/yourname/english-cli
cd english-cli
bash install.sh
```

または直接実行:

```bash
pip install anthropic rich click
python3 english_cli.py study
```

---

## Claude API の設定

Claude による添削・文法解説を使うには API キーが必要です。

```bash
# 方法1: コマンドで保存（~/.local/share/english-cli/progress.db に暗号化なしで保存）
english-cli config --api-key sk-ant-...

# 方法2: 環境変数（推奨）
export ANTHROPIC_API_KEY=sk-ant-...
```

API キーなしでも、文法ドリルと SRS レビューは使えます。

---

## コマンド一覧

| コマンド | 説明 |
|----------|------|
| `english-cli study` | 統合メニューで学習開始 |
| `english-cli grammar --level b2` | 文法ドリル（レベル指定） |
| `english-cli grammar --count 10` | 問題数指定 |
| `english-cli write` | 英作文（お題ランダム） |
| `english-cli write --topic "..."` | お題指定 |
| `english-cli review` | 単語SRSレビュー |
| `english-cli stats` | 学習統計・弱点分析 |
| `english-cli config --api-key KEY` | APIキー設定 |

---

## アーキテクチャ

```
english-cli/
├── english_cli.py      # メインアプリ（CLI・UI・全モード）
├── install.sh          # インストールスクリプト
└── progress.db         # SQLite（自動生成）
    ├── vocab_cards      # 単語カード（SRS情報付き）
    ├── study_sessions   # セッション履歴
    ├── writing_entries  # 英作文履歴
    ├── error_patterns   # 弱点タグ集計
    └── config           # 設定（APIキーなど）
```

### SRS アルゴリズム（SM-2）
- 評価 0-2（忘れた）→ 翌日再出題
- 評価 3-4（覚えた）→ 間隔 × 定着係数（初期 2.5）で次回日程を計算
- 定着係数は正解率に応じて 1.3〜3.0 の間で自動調整

---

## 今後の拡張アイデア

- 🎙 音声読み上げ（shadowing用スクリプト生成）
- 📊 週次レポートのグラフ表示
- 🗂 TOMLファイルで問題をカスタム追加
- 🌐 Ollama（ローカルLLM）対応（オフライン添削）
- 🔄 問題セットの共有・インポート

---

## ライセンス

MIT
