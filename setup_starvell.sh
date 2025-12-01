#!/bin/bash
set -e
REPO="exfador/starvell_api"
INSTALL_DIR="/opt/starvell_api"
SCREEN_NAME="starvell_api"
echo "Поехали..."
if [[ $EUID -ne 0 ]]; then
   echo "Нужен sudo для скачивания зависимостей."
   exit 1
fi
DISTRO_INFO=$(lsb_release -ds 2>/dev/null || (grep '^PRETTY_NAME=' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d \") || echo "Unknown")
echo "$DISTRO_INFO"
echo "Ставлю пакеты..."
apt-get update -qq
apt-get install -y -qq wget curl screen jq unrar-free ca-certificates gnupg lsb-release software-properties-common build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev libffi-dev liblzma-dev
CODENAME=$(lsb_release -cs 2>/dev/null || echo "bookworm")
python3.11 -V >/dev/null 2>&1 && PYTHON_INSTALLED=1 || PYTHON_INSTALLED=0
if [[ $PYTHON_INSTALLED -eq 0 ]]; then
    echo "Ставлю Python 3.11..."
    set +e
    apt-get update -qq
    apt-get install -y -qq python3.11 python3.11-venv python3.11-dev python3.11-distutils
    RC=$?
    if [[ $RC -ne 0 ]]; then
        add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1 || true
        mkdir -p /etc/apt/keyrings
        wget -qO- https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu/gpg.key | gpg --dearmor -o /etc/apt/keyrings/deadsnakes.gpg || true
        echo "deb [signed-by=/etc/apt/keyrings/deadsnakes.gpg] https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu $CODENAME main" > /etc/apt/sources.list.d/deadsnakes.list
        apt-get update -qq || true
        apt-get install -y -qq python3.11 python3.11-venv python3.11-dev python3.11-distutils || true
    fi
    set -e
fi
if ! command -v python3.11 &> /dev/null; then
    echo "Не удалось установить Python 3.11 по неизвестной причине"
    exit 1
fi
command -v pip3.11 >/dev/null 2>&1 || curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 - --break-system-packages || curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 -
python3.11 -m pip install --upgrade pip -q
echo "Качаю последний релиз с GitHub..."
LATEST_RELEASE=$(curl -s "https://api.github.com/repos/$REPO/releases/latest")
RELEASE_URL=$(echo "$LATEST_RELEASE" | jq -r '.assets[] | select(.name | endswith(".rar")) | .browser_download_url' | head -n1)
if [[ -z "$RELEASE_URL" ]]; then
    echo "Не нашел .rar архив в релизе"
    exit 1
fi
echo "Распаковываю скачанный релиз..."
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"
wget -q -O release.rar "$RELEASE_URL"
unrar x -o+ release.rar || true
rm -f release.rar
shopt -s dotglob nullglob
dirs=(*/)
if [ ${#dirs[@]} -eq 1 ] && [ -d "${dirs[0]}" ]; then
    mv "${dirs[0]}"* .
    rmdir "${dirs[0]}"
fi
shopt -u dotglob nullglob
if [ -f "requirements.txt" ]; then
    echo "Ставлю зависимости (requirements.txt)..."
    python3.11 -m pip install -q -r requirements.txt --break-system-packages || python3.11 -m pip install -q -r requirements.txt
fi
MAIN_FILE="run_bot.py"
if [[ ! -f "$MAIN_FILE" ]]; then
    echo "Не нашел $MAIN_FILE"
    exit 1
fi
screen -S "$SCREEN_NAME" -X quit 2>/dev/null || true
sleep 1
echo ""
echo "Готово! Запускаю бота..."
echo "Сейчас бот долежн запросить данные - введи их"
echo "После настройки нажми Ctrl+A, потом D для выхода"
echo ""
sleep 2
cd "$INSTALL_DIR"
screen -AdmS "$SCREEN_NAME" python3.11 "$MAIN_FILE"