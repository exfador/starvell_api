#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_URL="https://github.com/exfador/starvell_api.git"
INSTALL_DIR="${STARVELL_INSTALL_DIR:-$PWD/starvell_api}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v git >/dev/null 2>&1 || ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Нужны git и Python 3.11+. Установите их пакетным менеджером системы." >&2
    exit 1
fi

"$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {
    echo "Требуется Python 3.11 или новее." >&2
    exit 1
}

if [ -e "$INSTALL_DIR" ] && [ "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
    echo "Каталог уже существует и не пуст: $INSTALL_DIR" >&2
    echo "Укажите другой путь через STARVELL_INSTALL_DIR." >&2
    exit 1
fi

VERSION="${STARVELL_VERSION:-}"
if [ -z "$VERSION" ]; then
    VERSION="$(git ls-remote --tags --sort='-v:refname' "$REPOSITORY_URL" \
        | awk -F/ '!/\^\{\}$/ {print $3; exit}')"
fi
if [ -z "$VERSION" ]; then
    echo "Не удалось определить последнюю версию." >&2
    exit 1
fi

echo "Устанавливаю Starvell API $VERSION в $INSTALL_DIR"
git clone --depth 1 --branch "$VERSION" "$REPOSITORY_URL" "$INSTALL_DIR"
"$PYTHON_BIN" -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_DIR/.venv/bin/python" -m pip install -r "$INSTALL_DIR/requirements.txt"

echo
echo "Установка завершена. Запуск:"
echo "  cd '$INSTALL_DIR'"
echo "  .venv/bin/python run_bot.py"
