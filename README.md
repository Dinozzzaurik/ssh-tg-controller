# SSH-Telegram Bot

Телеграм бот для управления серверами через SSH. Запускается в Docker контейнере и позволяет выполнять команды на удаленном сервере.

## Возможности

- Полноценный интерактивный SSH терминал прямо в Telegram
- Выполнение команд на сервере в двух режимах:
  - Интерактивный терминал с сохранением состояния
  - Выполнение отдельных команд
- Ввод пароля прямо в чате с ботом
- Просмотр статуса сервера (аптайм, использование диска, память)
- Ограничение доступа по имени пользователя Telegram
- Удобные кнопки для управления (Ctrl+C, Ctrl+D, Exit)

## Требования

- Docker и Docker Compose
- Telegram бот token (получить у [@BotFather](https://t.me/BotFather))
- Данные для подключения к серверу по SSH

## Установка и настройка

1. Клонируйте репозиторий:

```bash
git clone <url-репозитория>
cd ssh-tg-bot
```

2. Создайте файл `.env` на основе примера:

```bash
cp .env.example .env
```

3. Отредактируйте файл `.env`, указав свои данные:
   - `TELEGRAM_TOKEN` - токен вашего Telegram бота
   - `AUTHORIZED_USER` - ваше имя пользователя в Telegram (без @)
   - `SERVER_IP` - IP-адрес сервера
   - `SSH_USERNAME` - имя пользователя для SSH (по умолчанию root)
   - `SSH_PASSWORD` - пароль для SSH (опционально, можно ввести в чате)

## Запуск

```bash
docker-compose up -d
```

## Использование

После запуска бота отправьте ему команду `/start` или `/help`, чтобы получить список доступных команд.

### Доступные команды

- `/terminal` - Запустить интерактивный SSH терминал
- `/cmd <команда>` - Выполнить одиночную команду на сервере
- `/status` - Проверить статус сервера
- `/password` - Установить пароль для SSH подключения (вводится в чате)
- `/exit` - Выйти из режима терминала

### Работа с терминалом

После запуска команды `/terminal` бот создает интерактивную сессию SSH. В этом режиме:

1. Все сообщения, отправленные боту, интерпретируются как команды терминала
2. Вывод команд отображается в виде отформатированного текста
3. Специальные кнопки позволяют отправлять Ctrl+C, Ctrl+D или выйти из терминала
4. Сессия сохраняет своё состояние между командами (например, если вы изменили директорию, она останется измененной для следующих команд)

### Примеры команд для терминала

```
ls -la
cd /var/log
cat syslog | grep -i error
htop
```

### Примеры одиночных команд

```
/cmd ls -la
/cmd df -h
/cmd systemctl status nginx
```

## Безопасность

- Бот проверяет имя пользователя Telegram, отклоняя запросы от неавторизованных пользователей
- SSH соединение устанавливается с использованием шифрования
- Сообщение с паролем автоматически удаляется после обработки
- Docker контейнер запускается с минимальными привилегиями

## Обслуживание

Просмотр логов:

```bash
docker-compose logs -f
```

Перезапуск бота:

```bash
docker-compose restart
```

Обновление (после изменения кода):

```bash
docker-compose build --no-cache
docker-compose up -d
``` 