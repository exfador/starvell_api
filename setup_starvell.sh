#!/bin/bash
set -e

REPO="exfador/starvell_api"
INSTALL_DIR="/opt/starvell_api"
SCREEN_NAME="starvell_api"
PY_SRC_VER="3.11.6"
MAIN_FILE="run_bot.py"

if [[ $EUID -ne 0 ]]; then
   echo "Нужен sudo"
   exit 1
fi

echo "Ставлю пакеты..."
apt-get update -qq
apt-get install -y -qq \
    wget curl screen jq ca-certificates gnupg lsb-release software-properties-common \
    build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev libffi-dev liblzma-dev git \
    libncursesw5-dev libgdbm-dev libnss3-dev uuid-dev \
    unrar p7zip-full || true

python3.11 -V >/dev/null 2>&1 && PYTHON_INSTALLED=1 || PYTHON_INSTALLED=0

if [[ $PYTHON_INSTALLED -eq 0 ]]; then
    echo "Ставлю Python 3.11..."
    set +e
    RC=1

    apt-get update -qq || true
    apt-get install -y -qq python3.11 python3.11-venv python3.11-dev python3.11-distutils >/dev/null 2>&1
    RC=$?

    if [[ $RC -ne 0 ]]; then
        echo "Собираю из исходников ${PY_SRC_VER}..."
        TMPDIR=$(mktemp -d)
        trap "rm -rf $TMPDIR" EXIT

        cd "$TMPDIR"
        wget -q "https://www.python.org/ftp/python/${PY_SRC_VER}/Python-${PY_SRC_VER}.tgz"
        tar -xf "Python-${PY_SRC_VER}.tgz"
        cd "Python-${PY_SRC_VER}"
        ./configure --enable-optimizations --with-ensurepip=install >/dev/null 2>&1
        make -j"$(nproc)" >/dev/null 2>&1 || make -j1 >/dev/null 2>&1
        make altinstall >/dev/null 2>&1
        RC=$?
    fi

    if [[ $RC -ne 0 ]]; then
        add-apt-repository -y ppa:deadsnakes/ppa >/dev/null 2>&1 || true
        apt-get update -qq || true
        apt-get install -y -qq python3.11 python3.11-venv python3.11-dev python3.11-distutils >/dev/null 2>&1
        RC=$?
    fi

    if [[ $RC -ne 0 ]]; then
        PYENV_ROOT="/root/.pyenv"
        git clone --depth 1 https://github.com/pyenv/pyenv.git "$PYENV_ROOT" >/dev/null 2>&1 || true
        export PYENV_ROOT="$PYENV_ROOT"
        export PATH="$PYENV_ROOT/bin:$PATH"
        if command -v pyenv >/dev/null 2>&1; then
            eval "$(pyenv init -)" >/dev/null 2>&1 || true
            pyenv install -s ${PY_SRC_VER} >/dev/null 2>&1
            pyenv global ${PY_SRC_VER} 2>/dev/null || true
            [ -x "$PYENV_ROOT/versions/${PY_SRC_VER}/bin/python3.11" ] && \
                ln -sf "$PYENV_ROOT/versions/${PY_SRC_VER}/bin/python3.11" /usr/local/bin/python3.11 && RC=0
        fi
    fi

    if [[ $RC -ne 0 ]]; then
        CONDA_PREFIX="/opt/miniconda3"
        wget -qO /tmp/miniconda.sh https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh || true
        bash /tmp/miniconda.sh -b -p "$CONDA_PREFIX" >/dev/null 2>&1 || true
        "$CONDA_PREFIX/bin/conda" create -y -p /opt/py311 python=3.11 >/dev/null 2>&1 || true
        [ -x /opt/py311/bin/python ] && ln -sf /opt/py311/bin/python /usr/local/bin/python3.11 && RC=0
        rm -f /tmp/miniconda.sh
    fi

    set -e
fi

if ! command -v python3.11 &> /dev/null; then
    echo "Не удалось установить Python 3.11"
    exit 1
fi

command -v pip3.11 >/dev/null 2>&1 || \
    (curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 - --break-system-packages || \
     curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 -)
python3.11 -m pip install --upgrade pip -q || true

echo "Качаю последний релиз..."
LATEST_RELEASE=$(curl -s "https://api.github.com/repos/$REPO/releases/latest")
RELEASE_URL=$(echo "$LATEST_RELEASE" | jq -r '.assets[] | select(.name | endswith(".rar")) | .browser_download_url' | head -n1)

if [[ -z "$RELEASE_URL" ]]; then
    echo "Не нашел .rar архив в релизе"
    exit 1
fi

echo "Распаковываю релиз..."
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

wget -q -O release.rar "$RELEASE_URL"

EXTRACT_OK=0
if command -v unrar >/dev/null 2>&1; then
    unrar x -o+ release.rar && EXTRACT_OK=1
elif command -v 7z >/dev/null 2>&1; then
    7z x release.rar -y >/dev/null 2>&1 && EXTRACT_OK=1
fi

if [[ $EXTRACT_OK -ne 1 ]]; then
    echo "Ошибка распаковки"
    exit 1
fi

rm -f release.rar

FOUND_PATH=$(find . -type f -name "$MAIN_FILE" | head -n1)

if [[ -z "$FOUND_PATH" ]]; then
    echo "$MAIN_FILE не найден после распаковки"
    ls -la
    exit 1
fi

FOUND_DIR=$(dirname "$FOUND_PATH")

if [[ "$FOUND_DIR" != "." ]]; then
    shopt -s dotglob nullglob
    mv "$FOUND_DIR"/* . 2>/dev/null || true
    rmdir "$FOUND_DIR" 2>/dev/null || true
    shopt -u dotglob nullglob
fi

if [[ ! -f "$MAIN_FILE" ]]; then
    echo "Не нашел $MAIN_FILE после перемещения"
    exit 1
fi

if [ -f "requirements.txt" ]; then
    python3.11 -m pip install -q -r requirements.txt --break-system-packages || \
        python3.11 -m pip install -q -r requirements.txt
fi

screen -S "$SCREEN_NAME" -X quit 2>/dev/null || true
sleep 1

echo "Готово! Запускаю бота в screen..."
echo "Нажми Ctrl+A, потом D для выхода"
sleep 2

cd "$INSTALL_DIR"
screen -AdmS "$SCREEN_NAME" python3.11 "$MAIN_FILE"
