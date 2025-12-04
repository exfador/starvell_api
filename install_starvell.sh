#!/bin/bash

sed -i 's/\r$//' "$0" >/dev/null 2>&1 #
if [ -z "$CLEANED_SCRIPT" ]; then #
    export CLEANED_SCRIPT=1 #
    exec bash "$0" "$@" #
fi #

set +e

echo "=== Информация о системе ==="
if [ -f /etc/os-release ]; then
    . /etc/os-release
    echo "ОС: $NAME"
    echo "Версия: $VERSION"
else
    echo "Не удалось определить версию ОС."
fi
echo "Ядро: $(uname -r)"
echo "============================"
echo ""

echo "Очистка старых репозиториев..."
grep -l "deadsnakes" /etc/apt/sources.list.d/* 2>/dev/null | xargs rm -f

apt-key adv --keyserver keyserver.ubuntu.com --recv-keys BA6932366A755776 >/dev/null 2>&1

set -e

echo "Обновление системы..."
apt update
apt install -y build-essential zlib1g-dev libncurses5-dev libgdbm-dev libnss3-dev libssl-dev libreadline-dev libffi-dev libsqlite3-dev wget libbz2-dev software-properties-common curl screen git

cd /tmp

if [ ! -f "Python-3.11.8.tgz" ]; then
    wget https://www.python.org/ftp/python/3.11.8/Python-3.11.8.tgz
fi

if [ ! -d "Python-3.11.8" ]; then
    tar -xf Python-3.11.8.tgz
fi

cd Python-3.11.8
./configure --enable-optimizations
make -j $(nproc)
make altinstall

cd ~
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

echo "Определение последней версии..."
LATEST_TAG=$(curl -s "https://api.github.com/repos/exfador/starvell_api/tags?page=1" | grep '"name":' | cut -d '"' -f 4 | grep -vE '^api$' | head -n 1)

if [ -z "$LATEST_TAG" ]; then
    echo "Не удалось определить версию. Используем по умолчанию 0.7"
    LATEST_TAG="0.7"
else
    echo "Найденная последняя версия: $LATEST_TAG"
fi

rm -rf starvell_api
mkdir -p starvell_api

wget -O starvell_api.tar.gz "https://github.com/exfador/starvell_api/archive/refs/tags/${LATEST_TAG}.tar.gz"

tar -xf starvell_api.tar.gz -C starvell_api --strip-components=1
rm starvell_api.tar.gz

cd starvell_api
python3.11 -m pip install -r requirements.txt

while true; do
    echo ""
    read -p "Введите имя для бота (будет 'starvell_api_ИМЯ'): " USER_NAME
    
    if [ -z "$USER_NAME" ]; then
        echo "Имя не может быть пустым."
        continue
    fi

    SESSION_NAME="starvell_api_${USER_NAME}"

    if screen -ls | grep -q "${SESSION_NAME}"; then
        echo "Ошибка: Screen сессия '${SESSION_NAME}' уже существует!"
        echo "Пожалуйста, выберите другое имя."
    else
        echo "Запуск бота в screen сессии '${SESSION_NAME}'..."
        screen -S "${SESSION_NAME}" python3.11 run_bot.py
        break
    fi
done
