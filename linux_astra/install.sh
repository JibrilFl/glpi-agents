#!/bin/bash

export PERL_MM_USE_DEFAULT=1
export PERL_AUTOINSTALL=--defaultdeps
export NONINTERACTIVE_TESTING=1
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "[1] Установка пакетов"
sudo apt update
sudo env DEBIAN_FRONTEND=noninteractive apt install -y perl make gcc curl ca-certificates pkg-config openssl libssl-dev libxml2 libxml2-dev zlib1g-dev cpanminus
CPANM="$(command -v cpanm)"
run_cpanm() {
    sudo env PERL_MM_USE_DEFAULT=1 PERL_AUTOINSTALL=--defaultdeps NONINTERACTIVE_TESTING=1 \
        "$CPANM" --notest --no-interactive --no-prompt "$@"
}

echo "[2] Установка зависимостей"
run_cpanm Module::Install

echo "[3] Распаковка агента"
cd /tmp
tar -xzf "$SCRIPT_DIR/glpi-agent-1.16.tar.gz"
cd glpi-agent-1.16

echo "[4] Установка Perl зависимостей"
run_cpanm --installdeps .

echo "[5] Установка агента"
sudo env PERL_MM_USE_DEFAULT=1 PERL_AUTOINSTALL=--defaultdeps NONINTERACTIVE_TESTING=1 perl Makefile.PL
sudo make
sudo make install

echo "[6] Установка XML зависимостей"
run_cpanm Net::SSLeay
run_cpanm --reinstall XML::LibXML

echo "[7] Копирование конфига"
mkdir -p /usr/local/etc/glpi-agent
cp "$SCRIPT_DIR/agent.cfg" /usr/local/etc/glpi-agent/agent.cfg

echo "[8] Создание сервиса"
cat <<EOF > /etc/systemd/system/glpi-agent.service
[Unit]
Description=GLPI Agent
After=network.target

[Service]
ExecStart=/usr/local/bin/glpi-agent --daemon --no-fork
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable glpi-agent
systemctl start glpi-agent

echo "[✔] Готово"
