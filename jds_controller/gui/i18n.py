from __future__ import annotations

import locale
import os
from typing import Dict

SUPPORTED_LANGS = {"ru", "en"}

TRANSLATIONS: Dict[str, Dict[str, str]] = {
    "ru": {
        "app_title": "JDS6600 Controller",
        "menu_file": "Файл",
        "menu_open": "Открыть…",
        "menu_save": "Сохранить",
        "menu_save_as": "Сохранить как…",
        "menu_new_template": "Новый шаблон",
        "menu_exit": "Выход",
        "menu_run": "Выполнение",
        "menu_start": "Старт",
        "menu_pause_resume": "Пауза/Продолжить",
        "menu_next_command": "Следующая команда (пропустить wait)",
        "menu_stop": "Стоп",
        "menu_validate": "Проверить CSV",
        "menu_enable_outputs_on_start": "Включать используемые каналы при старте",
        "menu_disable_outputs_on_finish": "Отключить каналы генератора по окончании",
        "menu_shutdown_pc_on_finish": "Выключить ПК по окончании",
        "menu_sound_on_finish": "Звуковые сигналы",
        "menu_help": "Справка",
        "menu_quick_help": "Краткая помощь\tF1",
        "menu_github": "GitHub…",
        "menu_about": "О программе",
        "menu_language": "Язык",
        "menu_view": "Вид",
        "menu_dark_theme": "Тёмная тема",
        "lang_ru": "Русский",
        "lang_en": "English",
        "group_connection": "Подключение",
        "label_port": "Порт:",
        "btn_refresh": "Обновить",
        "btn_find_connect": "Найти и подключиться",
        "btn_connect": "Подключиться",
        "btn_disconnect": "Отключиться",
        "btn_start_big": "СТАРТ",
        "btn_pause": "Пауза",
        "btn_resume": "Продолжить",
        "btn_next_command": "Следующая команда",
        "btn_stop": "Стоп",
        "label_default_channel": "Канал (по умолчанию):",
        "label_fixed_wait": "Фиксированный wait (сек):",
        "label_repeat_file": "Повтор файла",
        "group_commands_file": "Файл команд (редактируемый)",
        "group_log": "Лог выполнения",
        "group_now_playing": "Сейчас выполняется",
        "status_not_connected": "Не подключено",
        "status_connected": "Подключено",
        "device_unchecked": "не проверено",
        "device_not_selected": "не выбран",
        "device_checking": "проверка…",
        "device_connecting": "подключение…",
        "device_disconnecting": "отключение…",
        "device_found": "устройство найдено",
        "device_connected": "подключено",
        "device_not_found": "не найдено",
        "device_state_none": "Нет подключения",
        "device_state_no_data": "Подключено (нет данных)",
        "now_idle_primary": "Нет активной команды",
        "now_idle_secondary": "После запуска здесь появятся текущая частота и шаг",
        "now_wait_primary": "Ожидание",
        "now_wait_secondary": "Ожидание перед следующей командой",
        "now_freq_secondary": "Фиксированная частота",
        "now_cycle_primary": "Цикл",
        "now_cycle_secondary": "Cycle • {phase}",
        "now_mod_primary": "FM модуляция",
        "now_mod_secondary": "FM модуляция • {phase}",
        "phase_active": "выполнение",
        "phase_hold": "удержание частоты",
        "phase_pause": "пауза между частотами",
        "phase_rise": "подъём",
        "phase_fall": "спуск",
        "now_step_badge": "Шаг {current}/{total}",
        "status_searching_device": "Поиск устройства…",
        "status_connecting": "Подключение к {port}…",
        "status_disconnecting": "Отключение…",
        "status_connected_to": "Подключено: {port}",
        "status_port_operation_busy": "Подождите: операция с портом ещё выполняется.",
        "log_autodetect_start": "Авто-поиск устройства…",
        "title_check": "Проверка",
        "msg_commands_ok": "Файл команд корректен.",
        "title_error": "Ошибка",
        "title_csv_error": "Ошибка CSV",
        "title_open": "Открыть файл команд",
        "title_save_as": "Сохранить как",
        "title_unsaved": "Несохранённые изменения",
        "msg_unsaved": "Файл изменён. Сохранить изменения?",
        "msg_open_failed": "Не удалось открыть файл:\n{error}",
        "msg_save_failed": "Не удалось сохранить:\n{error}",
        "log_file_opened": "Открыт файл: {path}",
        "log_file_saved": "Сохранено: {path}",
        "msg_invalid_fixed_wait": "Неверное значение фиксированного wait (сек). Введите число >= 0.",
        "msg_select_port": "Выберите порт (или нажмите Авто-поиск).",
        "msg_connection_busy_wait": "Подождите, пока завершится текущая операция подключения/поиска.",
        "msg_port_access_denied": "Не удалось открыть порт {port}: доступ запрещён.\n\nОбычно это значит, что порт уже занят другой программой или у процесса нет прав доступа.\n\nДетали: {error}",
        "msg_port_busy": "Не удалось открыть порт {port}.\n\nПорт уже используется или недоступен. Закройте другие программы, работающие с этим COM/serial-портом, и попробуйте снова.\n\nДетали: {error}",
        "status_launching": "Запуск…",
        "status_restored_paused": "Восстановлено (пауза)…",
        "log_auto_resume_disabled_parse": "Auto-resume disabled: CSV parse error",
        "log_auto_resume": "== AUTO-RESUME (paused) ==",
        "log_pause": "== PAUSE ==",
        "log_resume": "== RESUME ==",
        "log_next_command": "== NEXT COMMAND (skip wait) ==",
        "log_stop": "== STOP requested ==",
        "title_browser_error": "Не удалось открыть браузер",
        "title_about": "О программе",
        "about_text": "JDS6600 Controller\n\nGUI/CLI утилита для управления генератором JDS6600.\nGitHub: {github}\nTelegram: {telegram} (@JcJet)",
        "log_connect_managed": "Во время выполнения сценария подключение управляется автоматически.",
        "log_port_not_selected": "Не выбран порт.",
        "log_port_operation_busy": "Подождите: предыдущая операция с портом ещё не завершилась.",
        "log_connect_busy": "Подключение к {port} ещё выполняется…",
        "log_disconnect_busy": "Отключение от {port} ещё выполняется…",
        "log_autodetect_busy": "Авто-поиск уже выполняется…",
        "log_connecting": "Подключение к {port}…",
        "log_connected": "Подключено к {port}",
        "log_disconnecting": "Отключение от {port}…",
        "log_disconnected": "Отключено от {port}",
        "status_device_found": "Найдено устройство: {port}",
        "log_autodetect_found": "Авто-поиск: найдено на {port}",
        "log_autodetect_skip_connected": "Авто-поиск пропущен: уже подключено к {port}",
        "status_device_not_found": "Устройство не найдено",
        "log_autodetect_not_found": "Авто-поиск: устройство не найдено",
        "title_connect_error": "Ошибка подключения",
        "log_connect_error": "Ошибка подключения: {error}",
        "status_stopped": "Остановлено",
        "status_done": "Готово",
        "status_repeat_restart": "Повтор файла: перезапуск",
        "log_repeat_restart": "Повтор файла: запуск заново",
        "log_outputs_off_on_finish": "По окончании: каналы генератора отключены.",
        "log_outputs_off_failed": "Не удалось отключить каналы генератора: {error}",
        "log_shutdown_pc_on_finish": "По окончании: выключение ПК…",
        "log_shutdown_pc_failed": "Не удалось запустить выключение ПК: {error}",
        "log_shutdown_pc_unsupported": "Выключение ПК не поддерживается на этой ОС.",
        "status_error": "Ошибка",
        "log_error": "ERROR: {error}",
        "log_status_poll_error": "Status poll error: {error}",
        "line_context": "Строка {line}:",
        "problematic_element": "Проблемный элемент #{pos}: {elem}",
        "log_autodetect_error": "Авто-поиск: {error}",
        "status_fm_mode": "Режим FM модуляции",
        "status_fm_mode_hz": "Режим FM модуляции: {freq} Hz",
        "status_fm_mode_hz_v": "Режим FM модуляции: {freq} Hz, {voltage} V",
        "log_mod_both_warning": "ВНИМАНИЕ! Модуляция запущена в режиме двух каналов. При тестировании в этом режиме была обнаружена нестабильность сигнала.\nРекомендуется выбрать один канал, добавив к комманде  {\"channel\":\"1\"}. И если требуется именно два канала, использовать функцию синхронизации в настройках генератора.",
        "filetypes_csv": "CSV files",
        "filetypes_all": "All files",
        "help_title": "Краткая помощь",
        "help_text": """Формат файла команд (CSV):\n\n  freq,<Hz>[,<опциональные настройки>]\n  wait,<секунды>\n  stop\n  cycle,[Hz1,Hz2,...],on=<сек>,off=<сек>[,<опциональные настройки>]\n\nПримеры:\n  freq,1000,{\"channel\":\"1+2\",\"waveform\":\"sine\",\"amplitude\":1.0}\n  wait,2\n  freq,2000,{\"channel\":1,\"waveform\":\"square\",\"dutycycle\":30,\"amplitude\":2.0}\n\n  cycle,[1000,2000,3000],on=5,off=10,{\"channel\":\"1+2\",\"waveform\":\"sine\",\"amplitude\":1.0}\n\nНастройки (3-й параметр) — рекомендуем строгий JSON (двойные кавычки, без лишних запятых).\nДля удобства допускается сокращённый вариант без кавычек у ключей/строк, но лучше писать JSON.\n\nРазделитель CSV определяется автоматически: запятая, точка-с-запятой или таб.\nЕсли редактируете в Excel/LibreOffice и файл ломается — попробуйте разделитель ';' или редактируйте здесь, в программе.\n\nКнопка «Следующая команда» пропускает текущий wait.\nОпция «Фиксированный wait» заменяет длительность всех wait во время выполнения.\nВ редакторе есть контекстное меню (ПКМ) и горячие клавиши копировать/вставить (Ctrl+C/Ctrl+V).""",
        "ctx_undo": "Отменить",
        "ctx_redo": "Повторить",
        "ctx_cut": "Вырезать",
        "ctx_copy": "Копировать",
        "ctx_paste": "Вставить",
        "ctx_select_all": "Выделить всё",
        "dialog_restart_not_needed": "",
    },
    "en": {
        "app_title": "JDS6600 Controller",
        "menu_file": "File",
        "menu_open": "Open…",
        "menu_save": "Save",
        "menu_save_as": "Save as…",
        "menu_new_template": "New template",
        "menu_exit": "Exit",
        "menu_run": "Run",
        "menu_start": "Start",
        "menu_pause_resume": "Pause/Resume",
        "menu_next_command": "Next command (skip wait)",
        "menu_stop": "Stop",
        "menu_validate": "Validate CSV",
        "menu_enable_outputs_on_start": "Enable used channels on start",
        "menu_disable_outputs_on_finish": "Disable generator outputs on finish",
        "menu_shutdown_pc_on_finish": "Shut down PC on finish",
        "menu_sound_on_finish": "Sound cues",
        "menu_help": "Help",
        "menu_quick_help": "Quick help\tF1",
        "menu_github": "GitHub…",
        "menu_about": "About",
        "menu_language": "Language",
        "menu_view": "View",
        "menu_dark_theme": "Dark theme",
        "lang_ru": "Русский",
        "lang_en": "English",
        "group_connection": "Connection",
        "label_port": "Port:",
        "btn_refresh": "Refresh",
        "btn_find_connect": "Find and connect",
        "btn_connect": "Connect",
        "btn_disconnect": "Disconnect",
        "btn_start_big": "START",
        "btn_pause": "Pause",
        "btn_resume": "Resume",
        "btn_next_command": "Next command",
        "btn_stop": "Stop",
        "label_default_channel": "Default channel:",
        "label_fixed_wait": "Fixed wait (sec):",
        "label_repeat_file": "Repeat file",
        "group_commands_file": "Command file (editable)",
        "group_log": "Execution log",
        "group_now_playing": "Now playing",
        "status_not_connected": "Not connected",
        "status_connected": "Connected",
        "device_unchecked": "not checked",
        "device_not_selected": "not selected",
        "device_checking": "checking…",
        "device_connecting": "connecting…",
        "device_disconnecting": "disconnecting…",
        "device_found": "device found",
        "device_connected": "connected",
        "device_not_found": "not found",
        "device_state_none": "No connection",
        "device_state_no_data": "Connected (no data)",
        "now_idle_primary": "No active command",
        "now_idle_secondary": "The current frequency and step will appear here after start",
        "now_wait_primary": "Waiting",
        "now_wait_secondary": "Waiting before the next command",
        "now_freq_secondary": "Fixed frequency",
        "now_cycle_primary": "Cycle",
        "now_cycle_secondary": "Cycle • {phase}",
        "now_mod_primary": "FM modulation",
        "now_mod_secondary": "FM modulation • {phase}",
        "phase_active": "running",
        "phase_hold": "holding frequency",
        "phase_pause": "pause between frequencies",
        "phase_rise": "rising",
        "phase_fall": "falling",
        "now_step_badge": "Step {current}/{total}",
        "status_searching_device": "Searching for device…",
        "status_connecting": "Connecting to {port}…",
        "status_disconnecting": "Disconnecting…",
        "status_connected_to": "Connected: {port}",
        "status_port_operation_busy": "Please wait: a port operation is still running.",
        "log_autodetect_start": "Auto-detecting device…",
        "title_check": "Validation",
        "msg_commands_ok": "Command file is valid.",
        "title_error": "Error",
        "title_csv_error": "CSV error",
        "title_open": "Open command file",
        "title_save_as": "Save as",
        "title_unsaved": "Unsaved changes",
        "msg_unsaved": "The file has been modified. Save changes?",
        "msg_open_failed": "Failed to open file:\n{error}",
        "msg_save_failed": "Failed to save:\n{error}",
        "log_file_opened": "Opened file: {path}",
        "log_file_saved": "Saved: {path}",
        "msg_invalid_fixed_wait": "Invalid fixed wait value (sec). Enter a number >= 0.",
        "msg_select_port": "Select a port (or click Auto-detect).",
        "msg_connection_busy_wait": "Please wait until the current connection/search operation finishes.",
        "msg_port_access_denied": "Could not open port {port}: access denied.\n\nThis usually means another program is already using the port, or the process does not have the required permissions.\n\nDetails: {error}",
        "msg_port_busy": "Could not open port {port}.\n\nThe port is already in use or unavailable. Close other programs that may be using this COM/serial port and try again.\n\nDetails: {error}",
        "status_launching": "Starting…",
        "status_restored_paused": "Restored (paused)…",
        "log_auto_resume_disabled_parse": "Auto-resume disabled: CSV parse error",
        "log_auto_resume": "== AUTO-RESUME (paused) ==",
        "log_pause": "== PAUSE ==",
        "log_resume": "== RESUME ==",
        "log_next_command": "== NEXT COMMAND (skip wait) ==",
        "log_stop": "== STOP requested ==",
        "title_browser_error": "Could not open browser",
        "title_about": "About",
        "about_text": "JDS6600 Controller\n\nGUI/CLI utility for controlling the JDS6600 signal generator.\nGitHub: {github}\nTelegram: {telegram} (@JcJet)",
        "log_connect_managed": "During script execution the connection is managed automatically.",
        "log_port_not_selected": "No port selected.",
        "log_port_operation_busy": "Please wait: the previous port operation has not finished yet.",
        "log_connect_busy": "Connection to {port} is still in progress…",
        "log_disconnect_busy": "Disconnect from {port} is still in progress…",
        "log_autodetect_busy": "Auto-detect is already running…",
        "log_connecting": "Connecting to {port}…",
        "log_connected": "Connected to {port}",
        "log_disconnecting": "Disconnecting from {port}…",
        "log_disconnected": "Disconnected from {port}",
        "status_device_found": "Device found: {port}",
        "log_autodetect_found": "Auto-detect: found on {port}",
        "log_autodetect_skip_connected": "Auto-detect skipped: already connected to {port}",
        "status_device_not_found": "Device not found",
        "log_autodetect_not_found": "Auto-detect: device not found",
        "title_connect_error": "Connection error",
        "log_connect_error": "Connection error: {error}",
        "status_stopped": "Stopped",
        "status_done": "Done",
        "status_repeat_restart": "Repeat file: restarting",
        "log_repeat_restart": "Repeat file: starting again",
        "log_outputs_off_on_finish": "On finish: generator outputs were turned off.",
        "log_outputs_off_failed": "Could not disable generator outputs: {error}",
        "log_shutdown_pc_on_finish": "On finish: shutting down the PC…",
        "log_shutdown_pc_failed": "Could not initiate PC shutdown: {error}",
        "log_shutdown_pc_unsupported": "PC shutdown is not supported on this OS.",
        "status_error": "Error",
        "log_error": "ERROR: {error}",
        "log_status_poll_error": "Status poll error: {error}",
        "line_context": "Line {line}:",
        "problematic_element": "Problematic element #{pos}: {elem}",
        "log_autodetect_error": "Auto-detect: {error}",
        "status_fm_mode": "FM modulation mode",
        "status_fm_mode_hz": "FM modulation mode: {freq} Hz",
        "status_fm_mode_hz_v": "FM modulation mode: {freq} Hz, {voltage} V",
        "log_mod_both_warning": "WARNING! Modulation is running in dual-channel mode. Signal instability was observed during testing in this mode.\nIt is recommended to select a single channel by adding {\"channel\":\"1\"} to the command. If two channels are required, use the synchronization function in the generator settings.",
        "filetypes_csv": "CSV files",
        "filetypes_all": "All files",
        "help_title": "Quick help",
        "help_text": """Command file format (CSV):\n\n  freq,<Hz>[,<optional JSON options>]\n  wait,<seconds>\n  stop\n  cycle,[Hz1,Hz2,...],on=<sec>,off=<sec>[,<optional options>]\n\nExamples:\n  freq,1000,{\"channel\":\"1+2\",\"waveform\":\"sine\",\"amplitude\":1.0}\n  wait,2\n  freq,2000,{\"channel\":1,\"waveform\":\"square\",\"dutycycle\":30,\"amplitude\":2.0}\n\n  cycle,[1000,2000,3000],on=5,off=10,{\"channel\":\"1+2\",\"waveform\":\"sine\",\"amplitude\":1.0}\n\nFor the options object (3rd parameter), strict JSON is recommended\n(double quotes, no trailing commas).\nA relaxed shorthand without quotes for keys/strings is accepted for convenience,\nbut proper JSON is preferred.\n\nThe CSV delimiter is auto-detected: comma, semicolon or tab.\nIf editing in Excel/LibreOffice breaks the file, try ';' or edit it directly here.\n\nThe “Next command” button skips the current wait.\nThe “Fixed wait” option overrides all wait durations during execution.\nThe editor has a context menu (right-click) and copy/paste shortcuts (Ctrl+C/Ctrl+V).""",
        "ctx_undo": "Undo",
        "ctx_redo": "Redo",
        "ctx_cut": "Cut",
        "ctx_copy": "Copy",
        "ctx_paste": "Paste",
        "ctx_select_all": "Select all",
        "dialog_restart_not_needed": "",
    },
}


def detect_language(saved_value: str | None = None) -> str:
    cand = (saved_value or "").strip().lower()
    if cand in SUPPORTED_LANGS:
        return cand
    probes = []
    try:
        probes.append(locale.getlocale()[0])
    except Exception:
        pass
    try:
        probes.append(locale.getdefaultlocale()[0])
    except Exception:
        pass
    probes.append(os.environ.get("LANG"))
    for item in probes:
        if not item:
            continue
        s = str(item).lower()
        if s.startswith("ru"):
            return "ru"
        if s.startswith("en"):
            return "en"
    return "en"


def tr(lang: str, key: str, **kwargs) -> str:
    data = TRANSLATIONS.get(lang) or TRANSLATIONS["en"]
    text = data.get(key)
    if text is None:
        text = TRANSLATIONS["en"].get(key, key)
    try:
        return text.format(**kwargs)
    except Exception:
        return text


def translate_runtime_text(text: str, target_lang: str) -> str:
    import re

    value = str(text)
    for key in set(TRANSLATIONS["ru"]).union(TRANSLATIONS["en"]):
        ru = TRANSLATIONS["ru"].get(key)
        en = TRANSLATIONS["en"].get(key)
        if value == ru or value == en:
            return tr(target_lang, key)

    # Dynamic runtime messages emitted from runner/device-state callbacks.
    m = re.fullmatch(r"Режим FM модуляции", value)
    if m:
        return tr(target_lang, "status_fm_mode")

    m = re.fullmatch(r"Режим FM модуляции: ([0-9]+(?:\.[0-9]+)?) Hz", value)
    if m:
        return tr(target_lang, "status_fm_mode_hz", freq=m.group(1))

    m = re.fullmatch(r"Режим FM модуляции: ([0-9]+(?:\.[0-9]+)?) Hz, ([0-9]+(?:\.[0-9]+)?) V", value)
    if m:
        return tr(target_lang, "status_fm_mode_hz_v", freq=m.group(1), voltage=m.group(2))

    if value == TRANSLATIONS["ru"].get("log_mod_both_warning") or value == TRANSLATIONS["en"].get("log_mod_both_warning"):
        return tr(target_lang, "log_mod_both_warning")

    return value
