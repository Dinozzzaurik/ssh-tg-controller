import paramiko
import os
import logging
import time
from threading import Thread
import queue
import re

class SSHManager:
    def __init__(self, server_ip=None, username=None, password=None, key_path=None):
        self.server_ip = server_ip or os.getenv('SERVER_IP')
        self.username = username or os.getenv('SSH_USERNAME', 'root')
        self.password = password or os.getenv('SSH_PASSWORD')
        # По умолчанию не используем ключ, если не передан явно
        self.key_path = None
        if key_path and os.path.isfile(key_path):
            self.key_path = key_path
        
        if not self.server_ip:
            raise ValueError("Server IP is required")
        
        self.client = None
        self.shell = None
        self.shell_session_active = False
        self.output_queue = queue.Queue()
        self.logger = logging.getLogger(__name__)
    
    def set_password(self, password):
        """Set SSH password manually"""
        self.password = password
        return True
    
    def connect(self):
        """Establish SSH connection to the server"""
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            connect_kwargs = {
                'hostname': self.server_ip,
                'username': self.username,
                'timeout': 10
            }
            
            if self.password:
                connect_kwargs['password'] = self.password
                self.logger.info(f"Подключение с использованием пароля")
            elif self.key_path and os.path.isfile(self.key_path):
                connect_kwargs['key_filename'] = self.key_path
                self.logger.info(f"Подключение с использованием SSH ключа: {self.key_path}")
            else:
                self.logger.error("Не указан пароль для подключения")
                return False
            
            self.client.connect(**connect_kwargs)
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to {self.server_ip}: {str(e)}")
            return False
    
    def disconnect(self):
        """Close SSH connection"""
        self.stop_shell_session()
        if self.client:
            self.client.close()
            self.client = None
    
    def execute_command(self, command):
        """Execute command on the remote server"""
        if not self.client:
            if not self.connect():
                return False, "Failed to connect to server"
        
        try:
            stdin, stdout, stderr = self.client.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()
            
            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')
            
            if exit_status != 0:
                return False, f"Command failed: {error or output}"
            
            return True, output
        except Exception as e:
            self.logger.error(f"Error executing command: {str(e)}")
            return False, f"Error: {str(e)}"
    
    def start_shell_session(self):
        """Start an interactive shell session"""
        if self.shell_session_active:
            return True, "Shell session already active"
        
        if not self.client:
            if not self.connect():
                return False, "Failed to connect to server"
        
        try:
            # Открываем интерактивную сессию
            self.shell = self.client.invoke_shell()
            self.shell.settimeout(0.0)  # Неблокирующий режим
            
            # Запускаем поток для чтения вывода
            self.shell_session_active = True
            Thread(target=self._read_shell_output, daemon=True).start()
            
            # Ждем, пока shell не будет готов
            time.sleep(1)
            
            # Получаем первоначальный вывод (обычно приветствие и промпт)
            initial_output = ""
            try:
                while not self.output_queue.empty():
                    initial_output += self.output_queue.get_nowait()
            except queue.Empty:
                pass
            
            return True, initial_output
        except Exception as e:
            self.logger.error(f"Error starting shell session: {str(e)}")
            return False, f"Error: {str(e)}"
    
    def stop_shell_session(self):
        """Stop the interactive shell session"""
        self.shell_session_active = False
        if self.shell:
            self.shell.close()
            self.shell = None
        
        # Очищаем очередь вывода
        while not self.output_queue.empty():
            try:
                self.output_queue.get_nowait()
            except queue.Empty:
                break
    
    def send_shell_command(self, command):
        """Send a command to the active shell session"""
        if not self.shell_session_active or not self.shell:
            success, message = self.start_shell_session()
            if not success:
                return False, message
        
        try:
            # Исправление типичных проблем с Unicode символами
            # Заменяем длинное тире (em dash) на два дефиса
            command = command.replace('—', '--')
            
            # Для команды ls сначала задаем настройки среды для лучшего вывода
            is_ls_command = command.strip().startswith("ls ") or command.strip() == "ls"
            if is_ls_command:
                # Перед выполнением ls настраиваем псевдо-терминал для лучшего вывода
                self.shell.send("export COLUMNS=100\n")
                self.shell.send("export TERM=dumb\n")
                time.sleep(0.2)  # Даем время на применение настроек
            
            # Очищаем очередь вывода перед отправкой команды
            while not self.output_queue.empty():
                try:
                    self.output_queue.get_nowait()
                except queue.Empty:
                    break
            
            # Отправляем команду и символ новой строки 
            self.shell.send(command + "\n")
            
            # Даем время на выполнение команды
            time.sleep(1.0)  # Увеличиваем время ожидания начала вывода
            
            # Собираем вывод
            output = ""
            timeout = 10  # Увеличиваем максимальное время ожидания вывода
            prompt_wait = 2.0  # Дополнительное время ожидания после обнаружения промпта
            start_time = time.time()
            last_output_time = time.time()
            prompt_found = False
            prompt_found_time = None
            
            while time.time() - start_time < timeout:
                try:
                    # Получаем данные из очереди, но не блокируем выполнение надолго
                    chunk = self.output_queue.get(timeout=0.1)
                    output += chunk
                    last_output_time = time.time()
                    
                    # Проверяем наличие приглашения командной строки (prompt)
                    if re.search(r'[\$#>]\s*$', chunk):
                        # Нашли промпт - запоминаем время
                        if not prompt_found:
                            prompt_found = True
                            prompt_found_time = time.time()
                        # Но не выходим сразу, а ждем дополнительное время для буферизованного вывода
                except queue.Empty:
                    # Проверяем, должны ли мы выйти по тайм-ауту
                    if prompt_found and time.time() - prompt_found_time > prompt_wait:
                        # Прошло достаточно времени после обнаружения промпта
                        break
                    
                    # Если давно не было вывода и есть данные и прошло достаточно времени
                    current_time = time.time()
                    if output and current_time - last_output_time > 2.0:
                        # Проверяем наличие промпта во всем выводе
                        if re.search(r'[\$#>]\s*$', output):
                            # Нашли промпт в общем выводе
                            break
                    continue
            
            # Даем дополнительное время для получения всего вывода
            time.sleep(0.5)
            
            # Проверяем, есть ли еще данные в очереди
            while not self.output_queue.empty():
                try:
                    chunk = self.output_queue.get(timeout=0.1)
                    output += chunk
                except queue.Empty:
                    break
            
            # Извлекаем ошибки если они есть
            error_pattern = r'((?:error|failed|no such|not found).*?)(?:\n|$)'
            errors = re.findall(error_pattern, output.lower())
            
            # Очищаем вывод от ввода команды и приглашения командной строки
            processed_lines = []
            lines = output.splitlines()
            
            # Флаг для отслеживания первой строки (эхо команды)
            first_line_skipped = False
            
            for line in lines:
                # Пропускаем первую строку, если она содержит введенную команду
                if not first_line_skipped and (command in line or line.strip() == ''):
                    first_line_skipped = True
                    continue
                
                # Удаляем ANSI escape-коды и другие служебные символы
                line = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', line)
                # Удаляем коды readline ([?2004l и [?2004h)
                line = re.sub(r'\[\?2004[lh]', '', line)
                # Удаляем другие возможные управляющие последовательности
                line = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', line)
                
                # Игнорируем строки, содержащие приглашение командной строки
                if re.search(r'[@:].*[#$>]', line) or not line.strip():
                    continue
                
                # Специальная обработка для вывода команды ls
                if is_ls_command:
                    # Заменяем последовательности из более чем 2-х пробелов на один или два пробела
                    line = re.sub(r' {3,}', '  ', line)
                
                # Добавляем очищенную строку в результат
                if line.strip():
                    processed_lines.append(line)
            
            # Формируем итоговый вывод
            cleaned_output = "\n".join(processed_lines)
            
            # Если это была команда ls, улучшаем форматирование вывода
            if is_ls_command:
                # Обрабатываем вывод ls для более аккуратного отображения
                formatted_output = self._format_ls_output(cleaned_output)
                return True, formatted_output
            
            # Если нашли ошибки и выход пустой, возвращаем ошибку
            if errors and not cleaned_output.strip():
                return False, "\n".join(errors)
            
            return True, cleaned_output
        except Exception as e:
            self.logger.error(f"Error sending command to shell: {str(e)}")
            return False, f"Error: {str(e)}"
    
    def _format_ls_output(self, output):
        """Форматирует вывод команды ls для лучшей читаемости"""
        lines = output.splitlines()
        files = []
        
        # Удаляем пустые строки и собираем файлы
        for line in lines:
            line = line.strip()
            if not line:
                continue
                
            # Пропускаем строки с командными промптами
            if re.search(r'[@:].*[#$>]', line):
                continue
                
            # Корректная обработка файлов с пробелами, заключенных в кавычки
            current = ""
            in_quotes = False
            quote_char = None
            
            # Обрабатываем строку символ за символом
            for char in line + ' ':  # Добавляем пробел в конец для обработки последнего файла
                if (char == "'" or char == '"') and not in_quotes:
                    # Начало строки в кавычках
                    in_quotes = True
                    quote_char = char
                    current += char
                elif char == quote_char and in_quotes:
                    # Конец строки в кавычках
                    in_quotes = False
                    quote_char = None
                    current += char
                elif char.isspace() and not in_quotes:
                    # Пробел вне кавычек - разделитель файлов
                    if current:
                        files.append(current.strip())
                        current = ""
                else:
                    # Обычный символ
                    current += char
        
        # Если файлов нет, возвращаем оригинальный вывод
        if not files:
            return output
            
        # Сортируем файлы для лучшей читаемости
        files.sort()
        
        # Определяем максимальную длину имени файла для вычисления ширины колонки
        max_length = max(len(file) for file in files) + 2  # +2 для дополнительного пространства
        
        # Форматируем вывод в таблицу с 3 колонками
        columns = 3
        rows = (len(files) + columns - 1) // columns  # Округление вверх для определения числа строк
        
        result = []
        
        for row in range(rows):
            row_files = []
            for col in range(columns):
                idx = row + col * rows
                if idx < len(files):
                    # Форматируем каждый файл по фиксированной ширине колонки
                    file_entry = files[idx].ljust(max_length)
                    row_files.append(file_entry)
            
            result.append("".join(row_files))
        
        return "\n".join(result)
    
    def _read_shell_output(self):
        """Read output from the shell in a separate thread"""
        buffer_size = 8192  # Увеличиваем размер буфера
        
        while self.shell_session_active and self.shell:
            try:
                if self.shell.recv_ready():
                    # Считываем данные, пока они есть в буфере
                    data = self.shell.recv(buffer_size).decode('utf-8', errors='replace')
                    self.output_queue.put(data)
                    
                    # Продолжаем считывать, пока есть данные
                    read_attempts = 0
                    max_read_attempts = 5
                    
                    while read_attempts < max_read_attempts:
                        if self.shell.recv_ready():
                            more_data = self.shell.recv(buffer_size).decode('utf-8', errors='replace')
                            if more_data:
                                self.output_queue.put(more_data)
                                read_attempts = 0  # Сбрасываем счетчик, если получили данные
                            else:
                                read_attempts += 1
                        else:
                            read_attempts += 1
                        time.sleep(0.05)  # Маленькая пауза между проверками
                else:
                    # Нет данных - ждем немного
                    time.sleep(0.1)
            except Exception as e:
                self.logger.error(f"Error reading from shell: {str(e)}")
                self.shell_session_active = False
                break 