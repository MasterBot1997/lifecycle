import os

import gspread

SPREADSHEET_ID = "1V1nrBJLsVefUEKj6HXEgimywoBIvGINnplu1f9p4luI"
SHEET_GID = 640640423
CLOUD_STATS_GID = 1877381753
CLOUD_MOVES_GID = 1373632618
ALL_GID = 2050386850

SERVICE_ACCOUNT_JSON = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "firm-amp-464221-g0-10e1752037bb.json",
)

HEADERS = [
    "ticket_id",
    "start_at",
    "end_at",
    "время_жизни_сек",
    "ожидание_ответа_саппорта_сек",
    "ожидание_ответа_клиента_сек",
    "время_жизни_мин",
    "ожидание_ответа_саппорта_мин",
    "ожидание_ответа_клиента_мин",
    "число_итераций",
    "lifecycle",
]

# Колонки, которые пишутся как формула =(секунды)/60
_FORMULA_COLS = {
    "время_жизни_мин":              "время_жизни_сек",
    "ожидание_ответа_саппорта_мин": "ожидание_ответа_саппорта_сек",
    "ожидание_ответа_клиента_мин":  "ожидание_ответа_клиента_сек",
}


def _col_letter(idx: int) -> str:
    """1-based column index → буква(ы): 1→A, 26→Z, 27→AA …"""
    s = ""
    while idx > 0:
        idx, r = divmod(idx - 1, 26)
        s = chr(65 + r) + s
    return s


def _fmt(val) -> str:
    if val is None:
        return ""
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    return str(val)


def write_to_sheet(rows: list, period_label: str) -> int:
    """Перезаписывает данные на листе начиная с row 2 (row 1 — заголовки).

    Колонки *_min записываются как Sheets-формула =(col_sec{row})/60.
    period_label используется только для вывода в консоль.
    """
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_JSON)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.get_worksheet_by_id(SHEET_GID)

    # Всегда пишем актуальные заголовки — они источник истины
    col_order = HEADERS
    ws.update("A1", [col_order])

    # Позиция каждой колонки (1-based) → буква
    col_letter_map = {h: _col_letter(i + 1) for i, h in enumerate(col_order)}

    # Очищаем данные с row 2 до конца
    last_col = _col_letter(len(col_order))
    ws.batch_clear([f"A2:{last_col}"])

    if not rows:
        print("Нет данных для записи.")
        return 0

    data = []
    for i, row in enumerate(rows):
        sheet_row = i + 2  # row 1 — заголовки
        data_row = []
        for h in col_order:
            if h in _FORMULA_COLS:
                sec_col = _FORMULA_COLS[h]
                sec_letter = col_letter_map.get(sec_col, "")
                data_row.append(f"={sec_letter}{sheet_row}/60")
            else:
                data_row.append(_fmt(row.get(h, "")))
        data.append(data_row)

    ws.update("A2", data, value_input_option="USER_ENTERED")
    print(f"Записано {len(data)} строк за {period_label} на лист (gid={SHEET_GID}).")
    return len(data)


def write_cloud_stats(rows: list, period_label: str) -> int:
    """Пишет месячную статистику cloud-тикетов в GID 1877381753.

    Структура листа: строка 2 — месяц, строка 3 — count_ticket, строка 4 — count_move.
    Каждый месяц — отдельная колонка, начиная с B. Существующие месяцы пропускаются.
    """
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_JSON)
    ws = gc.open_by_key(SPREADSHEET_ID).get_worksheet_by_id(CLOUD_STATS_GID)

    row2 = ws.row_values(2)
    existing_dates = set(row2[1:]) if len(row2) > 1 else set()
    next_col = max(len(row2) + 1, 2)

    updates = []
    for row in rows:
        month = row["month"]
        if month in existing_dates:
            print(f"  {month}: уже есть, пропускаем")
            continue
        letter = _col_letter(next_col)
        updates.append({
            "range": f"{letter}2:{letter}4",
            "values": [[month], [int(row["count_ticket"])], [int(row["count_move"])]],
        })
        print(f"  {month} → колонка {letter}")
        existing_dates.add(month)
        next_col += 1

    if updates:
        ws.batch_update(updates)
        print(f"Записано {len(updates)} колонок за {period_label} (gid={CLOUD_STATS_GID}).")
    else:
        print("Новых данных для записи нет.")
    return len(updates)


def write_cloud_moves(rows: list, period_label: str) -> int:
    """Пишет список тикетов с >1 перемещением на Вторую линию в GID 1373632618.

    Структура: строка 2 — заголовки, данные с A3.
    Перед записью очищает A3:D.
    """
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_JSON)
    ws = gc.open_by_key(SPREADSHEET_ID).get_worksheet_by_id(CLOUD_MOVES_GID)

    ws.batch_clear(["A3:D"])

    if not rows:
        print("Нет данных для сохранения.")
        return 0

    values = []
    for r in rows:
        values.append([
            r["ticket_id"],
            _fmt(r["created_at"]),
            int(r["count_move"]) if r["count_move"] is not None else 0,
            int(r["count_autorouting"]) if r["count_autorouting"] is not None else 0,
        ])

    ws.update(f"A3:D{2 + len(values)}", values, value_input_option="USER_ENTERED")
    print(f"Записано {len(values)} строк за {period_label} (gid={CLOUD_MOVES_GID}).")
    return len(values)


_ALL_HEADERS = [
    "ticket_id",
    "start_at",
    "end_at",
    "время_жизни_сек",
    "ожидание_ответа_саппорта_сек",
    "ожидание_ответа_клиента_сек",
    "время_жизни_мин",
    "ожидание_ответа_саппорта_мин",
    "ожидание_ответа_клиента_мин",
    "число_итераций",
    "кол_во_передач",
    "кол_во_передач_вручную",
    "lifecycle_history",
]

_ALL_FORMULA_COLS = {
    "время_жизни_мин":              "время_жизни_сек",
    "ожидание_ответа_саппорта_мин": "ожидание_ответа_саппорта_сек",
    "ожидание_ответа_клиента_мин":  "ожидание_ответа_клиента_сек",
}


def write_all(rows: list, period_label: str) -> int:
    """Lifecycle + count_move + count_manual_move → GID 2050386850.

    Перезаписывает лист полностью (строка 1 — заголовки, данные с A2).
    """
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_JSON)
    ws = gc.open_by_key(SPREADSHEET_ID).get_worksheet_by_id(ALL_GID)

    col_letter_map = {h: _col_letter(i + 1) for i, h in enumerate(_ALL_HEADERS)}
    last_col = _col_letter(len(_ALL_HEADERS))

    ws.update("A1", [_ALL_HEADERS])
    ws.batch_clear([f"A2:{last_col}"])

    if not rows:
        print("Нет данных для записи.")
        return 0

    data = []
    for i, row in enumerate(rows):
        sheet_row = i + 2
        data_row = []
        for h in _ALL_HEADERS:
            if h in _ALL_FORMULA_COLS:
                sec_col = _ALL_FORMULA_COLS[h]
                data_row.append(f"={col_letter_map[sec_col]}{sheet_row}/60")
            else:
                data_row.append(_fmt(row.get(h, "")))
        data.append(data_row)

    ws.update("A2", data, value_input_option="USER_ENTERED")
    print(f"Записано {len(data)} строк за {period_label} (gid={ALL_GID}).")
    return len(data)
