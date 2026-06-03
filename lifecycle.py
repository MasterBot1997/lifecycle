from datetime import datetime, timedelta
from typing import Dict, List

_MSK_OFFSET = timedelta(hours=3)


def _to_dt(val) -> datetime:
    """Парсит дату из БД (UTC) и переводит в московское время (UTC+3)."""
    if isinstance(val, datetime):
        return val + _MSK_OFFSET
    s = str(val)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt) + _MSK_OFFSET
        except ValueError:
            continue
    raise ValueError(f"Не удалось разобрать дату: {val!r}")


MAX_WAIT_SEC = 24 * 3600  # если от feedback до open прошло > 24ч — тикет считается закрытым


def _status_events(evts: list) -> list:
    return sorted(
        [e for e in evts if e["property"] == "status"],
        key=lambda x: (_to_dt(x["created_at"]), x["id"]),
    )


def _find_close_time(evts: list, fallback: datetime) -> datetime:
    """
    Время закрытия = последний feedback без следующего open в пределах 24ч.
    Если open пришёл спустя > 24ч — тоже считаем feedback финальным.
    Fallback: ticket.updated_at (если feedback-событий нет вообще).
    """
    last_feedback_dt = None
    for e in _status_events(evts):
        if e.get("new_value") == "feedback":
            last_feedback_dt = _to_dt(e["created_at"])
        elif e.get("new_value") == "open" and e.get("old_value") == "feedback" and last_feedback_dt:
            gap = (_to_dt(e["created_at"]) - last_feedback_dt).total_seconds()
            if gap <= MAX_WAIT_SEC:
                last_feedback_dt = None  # валидный ответ, итерация продолжается
            else:
                break  # > 24ч — feedback был финальным, дальше не смотрим
    return last_feedback_dt if last_feedback_dt is not None else fallback


def _calc_support_wait_sec(evts: list) -> int:
    """Суммарное время ожидания ответа от пользователя (только пары feedback→open ≤ 24ч)."""
    total = 0
    last_feedback_dt = None
    for e in _status_events(evts):
        if e.get("new_value") == "feedback":
            last_feedback_dt = _to_dt(e["created_at"])
        elif e.get("new_value") == "open" and e.get("old_value") == "feedback" and last_feedback_dt:
            gap = (_to_dt(e["created_at"]) - last_feedback_dt).total_seconds()
            if gap <= MAX_WAIT_SEC:
                total += int(gap)
                last_feedback_dt = None
            else:
                break  # > 24ч — тикет считался закрытым, дальше не учитываем
    return total


def _calc_iterations(evts: list) -> int:
    """
    Количество итераций: одна итерация = цикл [сообщения клиента → ответ(ы) саппорта → feedback].
    Считается как количество замкнутых пар feedback→open (≤ 24ч) + 1 (первая итерация всегда есть).
    """
    count = 0
    last_feedback_dt = None
    for e in _status_events(evts):
        if e.get("new_value") == "feedback":
            last_feedback_dt = _to_dt(e["created_at"])
        elif e.get("new_value") == "open" and e.get("old_value") == "feedback" and last_feedback_dt:
            gap = (_to_dt(e["created_at"]) - last_feedback_dt).total_seconds()
            if gap <= MAX_WAIT_SEC:
                count += 1
                last_feedback_dt = None
            else:
                break
    return count + 1


def build_lifecycle(ticket: dict, events: list) -> dict:
    """
    Строит lifecycle-сводку по одному тикету.

    ticket: строка из таблицы ticket (id, created_at, updated_at, status_id)
    events: все journal-события этого тикета (property in assignee/move/status)

    Возвращает dict:
      ticket_id, start_at, end_at, total_sec, work_sec, wait_sec,
      lifecycle (str), phases (list of dict)
    """
    ticket_id = ticket["id"]
    ticket_start = _to_dt(ticket["created_at"])

    # отфильтровать события до сортировки, чтобы передать в _find_close_time
    evts_all = sorted(
        [e for e in events if e["ticket_id"] == ticket_id],
        key=lambda e: (_to_dt(e["created_at"]), e["id"]),
    )
    ticket_end = _find_close_time(evts_all, fallback=_to_dt(ticket["updated_at"]))

    evts = evts_all
    phases: List[Dict] = []

    # ---- внутреннее состояние машины фаз ----
    state = "waiting"           # текущее состояние: waiting | working
    phase_start = ticket_start
    current_assignee = None
    phase_moves: List[Dict] = []
    phase_feedback = 0          # сколько раз отправили feedback в этой фазе
    phase_reopens = 0           # сколько раз пользователь ответил в фазе ожидания

    def _close_phase(end_dt: datetime, released: bool = False):
        """Закрыть текущую фазу и добавить в список."""
        nonlocal phase_moves, phase_feedback, phase_reopens
        dur = max(0, int((end_dt - phase_start).total_seconds()))
        ph: Dict = {
            "type": state,
            "start": phase_start,
            "end": end_dt,
            "duration_sec": dur,
        }
        if state == "working":
            ph["assignee"] = current_assignee
            ph["released"] = released       # True = сам отказался (Person → NULL)
            ph["moves"] = list(phase_moves)
            ph["feedback_count"] = phase_feedback
        else:
            ph["reopens"] = phase_reopens
        phases.append(ph)
        phase_moves = []
        phase_feedback = 0
        phase_reopens = 0

    for evt in evts:
        evt_dt = _to_dt(evt["created_at"])
        prop = evt["property"]
        old_val = evt.get("old_value")
        new_val = evt.get("new_value")

        if prop == "assignee":
            if old_val is None and new_val:
                # NULL → Person: кто-то взял тикет
                if state == "waiting":
                    _close_phase(evt_dt)
                    state = "working"
                    phase_start = evt_dt
                    current_assignee = new_val
                else:
                    # уже в работе, просто обновляем имя (авторутинг / передача внутри фазы)
                    current_assignee = new_val

            elif old_val and new_val is None:
                # Person → NULL: отказался от тикета
                if state == "working":
                    _close_phase(evt_dt, released=True)
                    state = "waiting"
                    phase_start = evt_dt
                    current_assignee = None

        elif prop == "move":
            if state == "working":
                phase_moves.append({"from": old_val, "to": new_val})

        elif prop == "status":
            if new_val == "feedback" and state == "working":
                # Сотрудник ответил клиенту → рабочая фаза заканчивается, начинается ожидание
                phase_feedback += 1
                _close_phase(evt_dt)
                state = "waiting"
                phase_start = evt_dt
                current_assignee = None
            elif new_val == "open" and old_val == "feedback" and state == "waiting":
                # пользователь ответил — тикет переоткрыт
                phase_reopens += 1

    # Закрыть последнюю фазу временем official close (ticket.updated_at)
    _close_phase(ticket_end, released=False)

    # ---- строим текстовый lifecycle ----
    parts = []
    for ph in phases:
        dur = ph["duration_sec"]
        if ph["type"] == "waiting":
            label = f"ожидание {dur}с"
            if ph.get("reopens", 0) > 1:
                label += f" [пользователь ответил x{ph['reopens']}]"
            elif ph.get("reopens", 0) == 1:
                label += " [пользователь ответил]"
            parts.append(label)
        else:
            who = ph.get("assignee") or "?"
            notes = []
            for m in ph.get("moves", []):
                notes.append(f"передан → {m['to']}")
            if ph.get("released"):
                notes.append("отказался")
            if ph.get("feedback_count", 0) > 0:
                notes.append("feedback")
            label = f"в работе {dur}с ({who})"
            if notes:
                label += f" [{', '.join(notes)}]"
            parts.append(label)

    lifecycle_str = " → ".join(parts) + " → тикет закрыт"

    время_жизни_сек = max(0, int((ticket_end - ticket_start).total_seconds()))
    ожидание_ответа_клиента_сек = _calc_support_wait_sec(evts_all)
    ожидание_ответа_саппорта_сек = max(0, время_жизни_сек - ожидание_ответа_клиента_сек)
    число_итераций = _calc_iterations(evts_all)

    return {
        "ticket_id": ticket_id,
        "start_at": ticket_start,
        "end_at": ticket_end,
        "время_жизни_сек": время_жизни_сек,
        "ожидание_ответа_саппорта_сек": ожидание_ответа_саппорта_сек,
        "ожидание_ответа_клиента_сек": ожидание_ответа_клиента_сек,
        "число_итераций": число_итераций,
        "lifecycle": lifecycle_str,
        "phases": phases,
    }


def collect_lifecycles(tickets: list, all_events: list) -> List[Dict]:
    """Собирает lifecycle по каждому тикету из списка."""
    events_by_ticket: Dict[int, List] = {}
    for e in all_events:
        events_by_ticket.setdefault(int(e["ticket_id"]), []).append(e)

    result = []
    for ticket in tickets:
        tid = int(ticket["id"])
        evts = events_by_ticket.get(tid, [])
        row = build_lifecycle(ticket, evts)
        result.append(row)
    return result
