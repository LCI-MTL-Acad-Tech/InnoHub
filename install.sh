#!/usr/bin/env bash
set -e

echo "Innovation Hub — installer"
echo ""

# ── System dependencies ───────────────────────────────────────────────────────
echo "Checking system dependencies..."

if ! command -v tesseract &> /dev/null; then
    echo "  Tesseract OCR not found. Installing..."
    sudo apt-get install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng
else
    echo "  Tesseract found: $(tesseract --version 2>&1 | head -1)"
    # Make sure French language pack is present
    if ! tesseract --list-langs 2>/dev/null | grep -q "^fra$"; then
        echo "  French language pack missing. Installing..."
        sudo apt-get install -y tesseract-ocr-fra
    else
        echo "  Tesseract languages: OK (eng, fra)"
    fi
fi
echo ""

# Generate man pages from source
echo "Generating man pages..."
python main.py --generate-man

# Install to user man path (no sudo needed)
MAN_DIR="$HOME/.local/share/man/man1"
mkdir -p "$MAN_DIR"
cp man/*.1 "$MAN_DIR/"

# Update man index
if command -v mandb &> /dev/null; then
    mandb "$HOME/.local/share/man" 2>/dev/null
elif command -v makewhatis &> /dev/null; then
    makewhatis "$HOME/.local/share/man"
fi

echo "Man pages installed."
echo "  Try: man innovhub"
echo "  Try: man innovhub-match, man innovhub-ingest, man innovhub-explain ..."
echo ""

# Optional shell alias
SCRIPT_PATH="$(realpath main.py)"
read -rp "Add 'innovhub' alias to ~/.bashrc? [y/N]: " ADD_ALIAS
if [[ "$ADD_ALIAS" =~ ^[Yy]$ ]]; then
    echo "alias innovhub='python $SCRIPT_PATH'" >> "$HOME/.bashrc"
    echo "Alias added. Run: source ~/.bashrc"
fi

echo ""
echo "Done. First run will download the embedding model (~400MB)."
