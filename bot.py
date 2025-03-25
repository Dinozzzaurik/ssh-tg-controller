import os
import logging
import warnings
import re
from dotenv import load_dotenv
from telegram import Update, ParseMode, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext, ConversationHandler, CallbackQueryHandler

from ssh_manager import SSHManager

# Игнорируем предупреждения для paramiko и telegram
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Состояния для разговора
WAITING_PASSWORD = 1
TERMINAL_MODE = 2

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()

# Получение настроек из переменных окружения
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
AUTHORIZED_USER = os.getenv('AUTHORIZED_USER')

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN environment variable is not set")

if not AUTHORIZED_USER:
    logger.warning("AUTHORIZED_USER is not set. Bot will be accessible to anyone.")

# Инициализация SSH менеджера
ssh_manager = SSHManager()

# Словарь активных терминальных сессий по chat_id
active_sessions = {}

def check_authorization(update: Update) -> bool:
    """Проверка авторизации пользователя по тегу"""
    if not AUTHORIZED_USER:
        return True
    
    username = update.effective_user.username
    return username and username == AUTHORIZED_USER

def start(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /start"""
    if not check_authorization(update):
        update.message.reply_text("У вас нет доступа к этому боту.")
        return
    
    # Создаем клавиатуру с кнопками команд
    keyboard = [
        [KeyboardButton("/terminal"), KeyboardButton("/cmd")],
        [KeyboardButton("/status"), KeyboardButton("/password")],
        [KeyboardButton("Ctrl+C"), KeyboardButton("Ctrl+D")],
        [KeyboardButton("Restart container"), KeyboardButton("Reboot")],
        [KeyboardButton("/help")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    update.message.reply_text(
        "Привет! Я бот для управления сервером через SSH.\n\n"
        "Доступные команды:\n"
        "/terminal - Запустить интерактивный SSH терминал\n"
        "/cmd <команда> - Выполнить одиночную команду\n"
        "/status - Проверить статус сервера\n"
        "/password - Установить пароль для SSH подключения\n"
        "/exit - Выйти из режима терминала\n",
        reply_markup=reply_markup
    )

def connect_command(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /connect"""
    if not check_authorization(update):
        update.message.reply_text("У вас нет доступа к этому боту.")
        return
    
    # Проверяем, есть ли пароль
    if not ssh_manager.password:
        update.message.reply_text(
            "Не указан пароль для SSH подключения.\n"
            "Используйте команду /password для установки пароля."
        )
        return
    
    message = update.message.reply_text("Подключение к серверу...")
    
    if ssh_manager.connect():
        message.edit_text(f"✅ Успешно подключено к серверу {ssh_manager.server_ip}")
    else:
        message.edit_text("❌ Не удалось подключиться к серверу. Проверьте настройки и доступность сервера.")

def disconnect_command(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /disconnect"""
    if not check_authorization(update):
        update.message.reply_text("У вас нет доступа к этому боту.")
        return
    
    chat_id = update.effective_chat.id
    
    # Если была активная терминальная сессия, удаляем её
    if chat_id in active_sessions:
        del active_sessions[chat_id]
    
    ssh_manager.disconnect()
    update.message.reply_text("Отключено от сервера.")

def execute_command(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /cmd"""
    if not check_authorization(update):
        update.message.reply_text("У вас нет доступа к этому боту.")
        return
    
    if not context.args:
        update.message.reply_text("Пожалуйста, укажите команду.\nПример: /cmd ls -la")
        return
    
    command = ' '.join(context.args)
    message = update.message.reply_text(f"Выполнение команды: `{command}`...", parse_mode=ParseMode.MARKDOWN)
    
    success, output = ssh_manager.execute_command(command)
    
    if success:
        # Если вывод слишком длинный, обрезаем его
        if len(output) > 4000:
            output = output[:4000] + "...\n[Вывод слишком длинный и был обрезан]"
        
        message.edit_text(
            f"✅ Команда выполнена успешно:\n```\n{output}\n```",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        message.edit_text(
            f"❌ Ошибка при выполнении команды:\n```\n{output}\n```",
            parse_mode=ParseMode.MARKDOWN
        )

def start_terminal(update: Update, context: CallbackContext) -> int:
    """Запуск интерактивного терминала"""
    if not check_authorization(update):
        update.message.reply_text("У вас нет доступа к этому боту.")
        return ConversationHandler.END
    
    chat_id = update.effective_chat.id
    
    # Проверяем, есть ли пароль
    if not ssh_manager.password:
        update.message.reply_text(
            "Не указан пароль для SSH подключения.\n"
            "Используйте команду /password для установки пароля."
        )
        return ConversationHandler.END
    
    message = update.message.reply_text("Запуск интерактивного терминала...")
    
    # Отображаем индикатор ввода (бот печатает...)
    context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    # Запускаем сессию терминала
    success, output = ssh_manager.start_shell_session()
    
    if success:
        # Сохраняем информацию о текущей сессии
        active_sessions[chat_id] = True
        
        # Добавляем кнопки для управления терминалом
        keyboard = [
            [
                InlineKeyboardButton("Ctrl+C", callback_data="terminal_ctrl_c"),
                InlineKeyboardButton("Ctrl+D", callback_data="terminal_ctrl_d")
            ],
            [
                InlineKeyboardButton("Restart container", callback_data="terminal_restart_container"),
                InlineKeyboardButton("Reboot", callback_data="terminal_reboot")
            ],
            [InlineKeyboardButton("Выход (exit)", callback_data="terminal_exit")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Отключаем цветной вывод для улучшения читаемости
        ssh_manager.send_shell_command("export TERM=dumb")
        # Отключаем редактор строки (команды будут отображаться без лишних кодов)
        ssh_manager.send_shell_command("set +o emacs")
        ssh_manager.send_shell_command("stty -echo")
        
        # Получаем hostname для приветствия
        success, hostname = ssh_manager.send_shell_command("hostname")
        if not success or not hostname.strip():
            hostname = ssh_manager.server_ip
        
        # Очищаем и форматируем вывод приветствия
        # Удаляем лишние пустые строки и приглашение bash
        if output:
            output_lines = output.splitlines()
            # Удаляем ANSI escape-коды и другие служебные символы
            clean_lines = []
            for line in output_lines:
                # Удаляем ANSI escape-коды (цвета, форматирование)
                line = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', line)
                # Удаляем коды readline и другие управляющие последовательности
                line = re.sub(r'\[\?[0-9]*[a-z]', '', line)
                # Удаляем приглашение bash
                if not re.search(r'^[^:]*[\$#>]\s*$', line) and line.strip():
                    clean_lines.append(line)
            
            output = "\n".join(clean_lines)
        
        # Меняем приветствие в зависимости от успеха
        output_text = f"✅ **Терминал запущен**\n"
        output_text += f"📡 Подключен к: `{hostname.strip()}`\n"
        output_text += f"👤 Пользователь: `{ssh_manager.username}`\n\n"
        
        if output:
            output_text += f"```\n{output}\n```\n\n"
            
        output_text += "💻 *Отправляйте команды как обычные сообщения*\n"
        output_text += "🔴 Для выхода используйте /exit или кнопку ниже"
        
        message.edit_text(
            output_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        
        return TERMINAL_MODE
    else:
        message.edit_text(f"❌ Не удалось запустить терминал:\n```\n{output}\n```", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

def terminal_callback(update: Update, context: CallbackContext) -> int:
    """Обработка нажатий на кнопки в терминале"""
    query = update.callback_query
    query.answer()
    
    if not check_authorization(update):
        query.edit_message_text("У вас нет доступа к этому боту.")
        return ConversationHandler.END
    
    chat_id = update.effective_chat.id
    
    if query.data == "terminal_exit":
        # Отправляем команду exit в терминал
        ssh_manager.send_shell_command("exit")
        ssh_manager.stop_shell_session()
        
        # Удаляем информацию о сессии
        if chat_id in active_sessions:
            del active_sessions[chat_id]
        
        query.edit_message_text("Терминальная сессия завершена.")
        return ConversationHandler.END
    
    elif query.data == "terminal_ctrl_c":
        # Отправляем Ctrl+C в терминал
        ssh_manager.shell.send("\x03")
        context.bot.send_message(
            chat_id=chat_id,
            text="*Отправлен сигнал:* `Ctrl+C`",
            parse_mode=ParseMode.MARKDOWN
        )
        return TERMINAL_MODE
    
    elif query.data == "terminal_ctrl_d":
        # Отправляем Ctrl+D в терминал
        ssh_manager.shell.send("\x04")
        context.bot.send_message(
            chat_id=chat_id,
            text="*Отправлен сигнал:* `Ctrl+D`",
            parse_mode=ParseMode.MARKDOWN
        )
        return TERMINAL_MODE
    
    elif query.data == "terminal_restart_container":
        # Выполняем команду перезапуска контейнера
        context.bot.send_message(
            chat_id=chat_id,
            text="🔄 *Выполнение:* `docker compose up -d --build`",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Отображаем индикатор ввода
        context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        # Выполняем команду
        success, output = ssh_manager.send_shell_command("docker compose up -d --build")
        
        if success:
            context.bot.send_message(
                chat_id=chat_id,
                text="✅ *Контейнеры успешно перезапущены*\n```\n" + (output or "Нет вывода") + "\n```",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            context.bot.send_message(
                chat_id=chat_id,
                text="❌ *Ошибка при перезапуске контейнеров:*\n```\n" + output + "\n```",
                parse_mode=ParseMode.MARKDOWN
            )
        
        return TERMINAL_MODE
    
    elif query.data == "terminal_reboot":
        # Выполняем команду перезагрузки сервера
        context.bot.send_message(
            chat_id=chat_id,
            text="🔄 *Выполнение:* `reboot`\n⚠️ *Внимание:* Сервер будет перезагружен!",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Запрашиваем подтверждение перед перезагрузкой
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, перезагрузить", callback_data="terminal_reboot_confirm"),
                InlineKeyboardButton("❌ Отмена", callback_data="terminal_reboot_cancel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ *Подтвердите перезагрузку сервера*\nВы уверены, что хотите перезагрузить сервер?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        
        return TERMINAL_MODE
    
    elif query.data == "terminal_reboot_confirm":
        # Выполняем команду перезагрузки после подтверждения
        ssh_manager.send_shell_command("reboot")
        
        context.bot.send_message(
            chat_id=chat_id,
            text="🔄 *Сервер перезагружается...*\nПодключение будет потеряно. После перезагрузки запустите новую сессию.",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Закрываем сессию, так как сервер перезагружается
        ssh_manager.stop_shell_session()
        
        # Удаляем информацию о сессии
        if chat_id in active_sessions:
            del active_sessions[chat_id]
        
        return ConversationHandler.END
    
    elif query.data == "terminal_reboot_cancel":
        # Отмена перезагрузки
        context.bot.send_message(
            chat_id=chat_id,
            text="❌ *Перезагрузка отменена*",
            parse_mode=ParseMode.MARKDOWN
        )
        
        return TERMINAL_MODE
    
    elif query.data.startswith("terminal_cmd_"):
        # Извлекаем команду из callback_data
        cmd_name = query.data.replace("terminal_cmd_", "")
        
        # Добавляем опции к командам
        command_map = {
            "ls": "ls -la --color=never",
            "ps": "ps aux | head -20",
            "top": "top -n 1 -b",
            "htop": "htop -C -n 1",
            "df": "df -h",
            "free": "free -h",
            "uptime": "uptime",
            "w": "w",
            "netstat": "netstat -tuln",
            "ifconfig": "ifconfig || ip a"
        }
        
        command = command_map.get(cmd_name, cmd_name)
        
        # Отправляем команду и получаем вывод
        context.bot.send_message(
            chat_id=chat_id,
            text=f"*Выполнение команды:* `{command}`",
            parse_mode=ParseMode.MARKDOWN
        )
        
        # Отображаем индикатор ввода
        context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        success, output = ssh_manager.send_shell_command(command)
        
        if success:
            if not output.strip():
                output = "[Команда выполнена, нет вывода]"
            
            # Разбиваем длинный вывод на части
            if len(output) > 4000:
                parts = []
                for i in range(0, len(output), 4000):
                    parts.append(output[i:i+4000])
                
                for part in parts:
                    context.bot.send_message(
                        chat_id=chat_id,
                        text=f"```\n{part}\n```",
                        parse_mode=ParseMode.MARKDOWN
                    )
            else:
                context.bot.send_message(
                    chat_id=chat_id,
                    text=f"```\n{output}\n```",
                    parse_mode=ParseMode.MARKDOWN
                )
        else:
            context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Ошибка при выполнении команды:\n```\n{output}\n```",
                parse_mode=ParseMode.MARKDOWN
            )
    
    return TERMINAL_MODE

def terminal_command(update: Update, context: CallbackContext) -> int:
    """Обработчик команд в режиме терминала"""
    if not check_authorization(update):
        update.message.reply_text("У вас нет доступа к этому боту.")
        return ConversationHandler.END
    
    chat_id = update.effective_chat.id
    
    # Проверяем, активна ли сессия
    if chat_id not in active_sessions:
        update.message.reply_text("Терминальная сессия не активна. Запустите её с помощью /terminal")
        return ConversationHandler.END
    
    command = update.message.text
    
    # Выполняем команду немедленно
    success, output = ssh_manager.send_shell_command(command)
    
    # Проверяем результат
    if not success:
        update.message.reply_text(
            f"❌ Ошибка выполнения команды:\n```\n{output}\n```",
            parse_mode='Markdown'
        )
        return TERMINAL_MODE
    
    # Обрабатываем вывод
    if output:
        # Разбиваем вывод на части если он слишком длинный
        max_length = 4000  # Максимальная длина сообщения в Telegram
        
        if len(output) <= max_length:
            # Отправляем результат без inline клавиатуры
            try:
                update.message.reply_text(
                    f"```\n{output}\n```",
                    parse_mode='Markdown'
                )
            except Exception as e:
                # Если не удалось отформатировать (например, из-за разметки), отправляем без разметки
                update.message.reply_text(output)
        else:
            # Разбиваем вывод на части
            parts = [output[i:i+max_length] for i in range(0, len(output), max_length)]
            
            for i, part in enumerate(parts):
                if i == 0:
                    update.message.reply_text(
                        f"Часть {i+1}/{len(parts)}:\n```\n{part}\n```",
                        parse_mode='Markdown'
                    )
                else:
                    context.bot.send_message(
                        chat_id=chat_id,
                        text=f"Часть {i+1}/{len(parts)}:\n```\n{part}\n```",
                        parse_mode='Markdown'
                    )
    else:
        # Если вывода нет, просто показываем сообщение об успешном выполнении
        update.message.reply_text("✅ Команда выполнена успешно (нет вывода)")
    
    return TERMINAL_MODE

def status_command(update: Update, context: CallbackContext) -> None:
    """Обработчик команды /status"""
    if not check_authorization(update):
        update.message.reply_text("У вас нет доступа к этому боту.")
        return
    
    message = update.message.reply_text("Проверка статуса сервера...")
    
    # Проверяем подключение
    if not ssh_manager.client:
        if not ssh_manager.connect():
            message.edit_text("❌ Не удалось подключиться к серверу.")
            return
    
    # Получаем информацию о системе
    success, uptime = ssh_manager.execute_command("uptime")
    if not success:
        message.edit_text("❌ Не удалось получить данные о сервере.")
        return
    
    success, disk = ssh_manager.execute_command("df -h | grep -v tmpfs")
    success2, memory = ssh_manager.execute_command("free -h")
    
    status_text = f"📊 Статус сервера ({ssh_manager.server_ip}):\n\n"
    status_text += f"Аптайм:\n```\n{uptime.strip()}\n```\n\n"
    
    if success:
        status_text += f"Использование диска:\n```\n{disk.strip()}\n```\n\n"
    
    if success2:
        status_text += f"Использование памяти:\n```\n{memory.strip()}\n```"
    
    message.edit_text(status_text, parse_mode=ParseMode.MARKDOWN)

def request_password(update: Update, context: CallbackContext) -> int:
    """Запрос пароля для SSH"""
    if not check_authorization(update):
        update.message.reply_text("У вас нет доступа к этому боту.")
        return ConversationHandler.END
    
    update.message.reply_text(
        "Пожалуйста, введите пароль для SSH подключения.\n"
        "Внимание: После обработки ваше сообщение с паролем будет удалено для безопасности."
    )
    return WAITING_PASSWORD

def receive_password(update: Update, context: CallbackContext) -> int:
    """Получение пароля и его установка"""
    if not check_authorization(update):
        update.message.reply_text("У вас нет доступа к этому боту.")
        return ConversationHandler.END
    
    # Получаем пароль из сообщения
    password = update.message.text
    
    # Устанавливаем пароль
    ssh_manager.set_password(password)
    
    # Удаляем сообщение с паролем для безопасности
    try:
        update.message.delete()
    except Exception as e:
        logger.warning(f"Could not delete message with password: {e}")
    
    update.message.reply_text("✅ Пароль успешно установлен. Теперь можно подключиться к серверу командой /terminal или /cmd")
    
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext) -> int:
    """Отмена операции установки пароля"""
    if not check_authorization(update):
        update.message.reply_text("У вас нет доступа к этому боту.")
        return ConversationHandler.END
    
    update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

# Добавляем новый обработчик для кнопок меню
def handle_menu_buttons(update: Update, context: CallbackContext) -> None:
    """Обработчик кнопок из основного меню"""
    if not check_authorization(update):
        update.message.reply_text("У вас нет доступа к этому боту.")
        return
    
    command = update.message.text
    chat_id = update.effective_chat.id
    
    # Проверяем, активна ли сессия терминала
    is_terminal_active = chat_id in active_sessions
    
    if command == "Ctrl+C":
        if is_terminal_active and ssh_manager.shell:
            ssh_manager.shell.send("\x03")
            update.message.reply_text("*Отправлен сигнал:* `Ctrl+C`", parse_mode=ParseMode.MARKDOWN)
        else:
            update.message.reply_text("❌ Нет активной терминальной сессии. Запустите сессию командой /terminal")
    
    elif command == "Ctrl+D":
        if is_terminal_active and ssh_manager.shell:
            ssh_manager.shell.send("\x04")
            update.message.reply_text("*Отправлен сигнал:* `Ctrl+D`", parse_mode=ParseMode.MARKDOWN)
        else:
            update.message.reply_text("❌ Нет активной терминальной сессии. Запустите сессию командой /terminal")
    
    elif command == "Restart container":
        # Проверяем, есть ли соединение с сервером
        if not ssh_manager.client and not ssh_manager.connect():
            update.message.reply_text("❌ Не удалось подключиться к серверу")
            return
        
        message = update.message.reply_text("🔄 *Выполнение:* `docker compose up -d --build`", parse_mode=ParseMode.MARKDOWN)
        context.bot.send_chat_action(chat_id=chat_id, action="typing")
        
        # Используем send_shell_command вместо execute_command для лучшей обработки параметров
        if is_terminal_active and ssh_manager.shell:
            # Если уже есть активная сессия, используем её
            success, output = ssh_manager.send_shell_command("cd /root/ssh-tg && docker compose up -d --build")
        else:
            # Иначе создаем новую сессию и выполняем команду
            if ssh_manager.connect():
                temp_active = True
                ssh_manager.start_shell_session()
                success, output = ssh_manager.send_shell_command("cd /root/ssh-tg && docker compose up -d --build")
                ssh_manager.stop_shell_session()
                temp_active = False
            else:
                success = False
                output = "Не удалось подключиться к серверу"
        
        if success:
            message.edit_text("✅ *Контейнеры успешно перезапущены*\n```\n" + (output or "Нет вывода") + "\n```", parse_mode=ParseMode.MARKDOWN)
        else:
            message.edit_text("❌ *Ошибка при перезапуске контейнеров:*\n```\n" + output + "\n```", parse_mode=ParseMode.MARKDOWN)
    
    elif command == "Reboot":
        # Проверяем, есть ли соединение с сервером
        if not ssh_manager.client and not ssh_manager.connect():
            update.message.reply_text("❌ Не удалось подключиться к серверу")
            return
        
        # Запрашиваем подтверждение
        keyboard = [
            [
                InlineKeyboardButton("✅ Да, перезагрузить", callback_data="reboot_confirm"),
                InlineKeyboardButton("❌ Отмена", callback_data="reboot_cancel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text(
            "⚠️ *Подтвердите перезагрузку сервера*\n"
            "Вы уверены, что хотите перезагрузить сервер?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

def general_callback_handler(update: Update, context: CallbackContext) -> None:
    """Обработчик callback-кнопок вне терминала"""
    query = update.callback_query
    query.answer()
    
    if not check_authorization(update):
        query.edit_message_text("У вас нет доступа к этому боту.")
        return
    
    chat_id = update.effective_chat.id
    
    if query.data == "reboot_confirm":
        if not ssh_manager.client and not ssh_manager.connect():
            query.edit_message_text("❌ Не удалось подключиться к серверу")
            return
        
        # Выполняем команду перезагрузки через interactive shell
        query.edit_message_text("🔄 *Выполнение команды перезагрузки...*", parse_mode=ParseMode.MARKDOWN)
        
        # Создаем временную сессию
        ssh_manager.start_shell_session()
        ssh_manager.send_shell_command("reboot")
        ssh_manager.stop_shell_session()
        
        query.edit_message_text("🔄 *Сервер перезагружается...*\n"
                               "Подключение будет потеряно. После перезагрузки запустите бота снова.",
                               parse_mode=ParseMode.MARKDOWN)
        
        # Закрываем все соединения
        ssh_manager.disconnect()
        
        # Очищаем все активные сессии
        active_sessions.clear()
    
    elif query.data == "reboot_cancel":
        query.edit_message_text("❌ *Перезагрузка отменена*", parse_mode=ParseMode.MARKDOWN)

def get_terminal_inline_keyboard():
    """Возвращает клавиатуру с кнопками для терминала"""
    keyboard = [
        [
            InlineKeyboardButton("Ctrl+C", callback_data="terminal_ctrl_c"),
            InlineKeyboardButton("Ctrl+D", callback_data="terminal_ctrl_d")
        ],
        [
            InlineKeyboardButton("Restart container", callback_data="terminal_restart_container"),
            InlineKeyboardButton("Reboot", callback_data="terminal_reboot")
        ],
        [InlineKeyboardButton("Выход (exit)", callback_data="terminal_exit")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_terminal_keyboard():
    """Возвращает основную клавиатуру для режима терминала"""
    keyboard = [
        [KeyboardButton("/terminal"), KeyboardButton("/cmd")],
        [KeyboardButton("/status"), KeyboardButton("/password")],
        [KeyboardButton("Ctrl+C"), KeyboardButton("Ctrl+D")],
        [KeyboardButton("Restart container"), KeyboardButton("Reboot")],
        [KeyboardButton("/help")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def main() -> None:
    """Запуск бота"""
    # Создаем Updater и передаем ему токен бота с увеличенным таймаутом
    updater = Updater(TELEGRAM_TOKEN, request_kwargs={'read_timeout': 30, 'connect_timeout': 30})

    # Получаем диспетчер для регистрации обработчиков
    dispatcher = updater.dispatcher

    # Создаем обработчик разговора для установки пароля
    password_handler = ConversationHandler(
        entry_points=[CommandHandler("password", request_password)],
        states={
            WAITING_PASSWORD: [MessageHandler(Filters.text & ~Filters.command, receive_password)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    
    # Создаем обработчик разговора для терминала
    terminal_handler = ConversationHandler(
        entry_points=[CommandHandler("terminal", start_terminal)],
        states={
            TERMINAL_MODE: [
                MessageHandler(Filters.text & ~Filters.command, terminal_command),
                CommandHandler("exit", lambda u, c: ConversationHandler.END),
                CallbackQueryHandler(terminal_callback, pattern="^terminal_")
            ]
        },
        fallbacks=[CommandHandler("exit", lambda u, c: ConversationHandler.END)]
    )

    # Регистрируем обработчики команд
    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", start))
    dispatcher.add_handler(CommandHandler("connect", connect_command))
    dispatcher.add_handler(CommandHandler("disconnect", disconnect_command))
    dispatcher.add_handler(CommandHandler("cmd", execute_command))
    dispatcher.add_handler(CommandHandler("status", status_command))
    
    # Добавляем обработчик для кнопок меню
    dispatcher.add_handler(MessageHandler(
        Filters.regex('^(Ctrl\\+C|Ctrl\\+D|Restart container|Reboot)$') & ~Filters.command, 
        handle_menu_buttons
    ))
    
    # Добавляем обработчик для callback-кнопок вне терминала
    dispatcher.add_handler(CallbackQueryHandler(general_callback_handler, pattern="^reboot_"))
    
    # Добавляем обработчик разговора для установки пароля
    dispatcher.add_handler(password_handler)
    
    # Добавляем обработчик терминала
    dispatcher.add_handler(terminal_handler)

    # Запускаем бота
    updater.start_polling()
    logger.info("Бот запущен")
    
    # Запускаем бота до нажатия Ctrl-C или получения сигнала остановки
    updater.idle()

if __name__ == '__main__':
    main() 