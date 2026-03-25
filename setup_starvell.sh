

set -e

REPO="exfador/starvell_api"
INSTALL_DIR="/root/starvell_api"

if [ "$EUID" -ne 0 ]; then
    echo "Запустите скрипт с правами root: sudo bash install_funpaycardinal.sh"
    exit 1
fi

echo "=== 1. Обновление системы и установка зависимостей ==="
apt update
apt install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev \
    libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev
apt install -y software-properties-common
add-apt-repository ppa:deadsnakes/ppa -y
apt update

echo "=== 2. Установка Python 3.11.8 ==="
cd /tmp
wget -q https://www.python.org/ftp/python/3.11.8/Python-3.11.8.tgz
tar -xf Python-3.11.8.tgz
cd Python-3.11.8
./configure --enable-optimizations
make -j $(nproc)
make altinstall
cd ~

echo "=== 3. Установка curl, jq, unrar, screen ==="
apt install -y curl jq unrar screen

echo "=== 4. Установка pip ==="
command -v pip3.11 >/dev/null 2>&1 || \
    (curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 - --break-system-packages || \
     curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 -)
python3.11 -m pip install --upgrade pip -q || true

echo "=== 5. Настройка русской локали ==="
apt-get install -y language-pack-ru
apt-get install -y language-pack-gnome-ru
apt-get install -y language-pack-kde-ru
update-locale LANG=ru_RU.utf8 2>/dev/null || true

echo "=== 6. Скачивание последнего релиза starvell_api ==="
echo "Качаю последний релиз..."
LATEST_RELEASE=$(curl -s "https://api.github.com/repos/$REPO/releases/latest")
RELEASE_URL=$(echo "$LATEST_RELEASE" | jq -r '.assets[] | select(.name | endswith(".rar")) | .browser_download_url' | head -n1)

if [[ -z "$RELEASE_URL" ]]; then
    echo "Не нашел .rar архив в релизе"
    exit 1
fi

RAR_NAME=$(echo "$LATEST_RELEASE" | jq -r '.assets[] | select(.name | endswith(".rar")) | .name' | head -n1)
echo "Скачиваю: $RELEASE_URL"
cd /tmp
wget -q --show-progress -O "$RAR_NAME" "$RELEASE_URL"

echo "=== 7. Распаковка архива ==="
mkdir -p "$INSTALL_DIR"
unrar x -o+ "$RAR_NAME" "$INSTALL_DIR/"
rm -f "$RAR_NAME"

echo "=== 8. Установка Python-зависимостей ==="
cd "$INSTALL_DIR"
if [ -f "requirements.txt" ]; then
    python3.11 -m pip install -r requirements.txt
fi
python3.11 -m pip install requests_toolbelt beautifulsoup4 telebot psutil cffi pymysql

echo ""
echo "=============================================="
echo "Установка завершена!"
echo "=============================================="
echo ""
echo "Для запуска starvell_api:"
echo "  1. screen -S stv"
echo "  2. cd $INSTALL_DIR"
echo "  3. python3.11 main.py"
echo ""
echo "Для фоновой работы: нажмите Ctrl+A, затем D"
echo "Для возврата в сессию: screen -r stv"
echo ""
