#!/usr/bin/env bash
# english-cli インストールスクリプト
set -e

INSTALL_DIR="$HOME/.local/bin"
DATA_DIR="$HOME/.local/share/english-cli"

echo "🎓 english-cli をインストールしています..."
echo ""

# Python チェック
if ! command -v python3 &>/dev/null; then
    echo "❌ Python 3 が見つかりません。"
    echo "   macOS:  brew install python"
    echo "   Ubuntu: sudo apt install python3 python3-pip"
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_VERSION" -lt 9 ]; then
    echo "❌ Python 3.9 以上が必要です（現在: 3.$PY_VERSION）"
    exit 1
fi

# pip パッケージ
echo "📦 依存パッケージをインストール中..."
python3 -m pip install anthropic rich click --quiet \
    --break-system-packages 2>/dev/null \
    || python3 -m pip install anthropic rich click --quiet

# ディレクトリ準備
mkdir -p "$DATA_DIR" "$INSTALL_DIR"

# スクリプト本体を配置
cp english_cli.py "$DATA_DIR/english_cli.py"

# exercises/ フォルダがあればコピー
if [ -d "exercises" ]; then
    cp -r exercises "$DATA_DIR/exercises"
    echo "📂 問題ファイル (exercises/) をコピーしました"
fi

# 実行用ラッパーを作成
cat > "$INSTALL_DIR/english-cli" << WRAPPER
#!/usr/bin/env bash
exec python3 "$DATA_DIR/english_cli.py" "\$@"
WRAPPER
chmod +x "$INSTALL_DIR/english-cli"

echo ""
echo "✅ インストール完了！"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  基本的な使い方:"
echo "    english-cli study       今日の学習を開始"
echo "    english-cli grammar     文法ドリル"
echo "    english-cli write       英作文 + Claude添削"
echo "    english-cli review      単語SRSレビュー"
echo "    english-cli stats       学習統計"
echo ""
echo "  Claude添削を使うには:"
echo "    english-cli config --api-key sk-ant-..."
echo "    または: export ANTHROPIC_API_KEY=sk-ant-..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# PATH チェック
if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    echo "⚠️  $INSTALL_DIR が PATH に含まれていません。"
    echo "   以下を ~/.zshrc または ~/.bashrc に追加してください:"
    echo ""
    echo '   export PATH="$HOME/.local/bin:$PATH"'
    echo ""
    echo "   追加後: source ~/.zshrc"
fi
