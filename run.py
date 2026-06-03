"""
Запуск:
  python run.py --task lifecycle   --from 2026-04-01 --to 2026-04-30
  python run.py --task cloud-stats --from 2025-04-01 --to 2026-05-01
  python run.py --task cloud-moves --from 2025-04-01 --to 2026-05-02

Флаги:
  --no-sheet     не писать в Google Sheets, только вывод в консоль
  --tag <slug>   тег AI (только для lifecycle, default: cloud_first)
  --ticket <id>  конкретный тикет (только для lifecycle)

Примечание: для cloud-stats и cloud-moves --to является исключающей границей
  (данные собираются за [--from, --to)).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gsheet import write_all, write_cloud_moves, write_cloud_stats, write_to_sheet
from lifecycle import collect_lifecycles
from queries import (
    get_closed_tickets,
    get_cloud_move_tickets,
    get_cloud_ticket_stats,
    get_journal_for_tickets,
    get_move_counts_for_tickets,
)


def _fmt_sec(sec: int) -> str:
    if sec < 60:
        return f"{sec}с"
    if sec < 3600:
        return f"{sec // 60}м {sec % 60}с"
    h = sec // 3600
    m = (sec % 3600) // 60
    return f"{h}ч {m}м"


def run_lifecycle(args):
    print(f"Запрос тикетов за {args.date_from} — {args.date_to}, тег: '{args.tag}' ...")
    tickets = get_closed_tickets(args.date_from, args.date_to, tag=args.tag)

    if args.ticket_id:
        tickets = [t for t in tickets if int(t["id"]) == args.ticket_id]
        if not tickets:
            print(f"Тикет {args.ticket_id} не найден или не закрыт / не имеет тег в этом диапазоне.")
            sys.exit(1)

    if not tickets:
        print("Тикетов не найдено.")
        sys.exit(0)

    print(f"Найдено тикетов: {len(tickets)}. Загружаем journal ...")
    ticket_ids = [int(t["id"]) for t in tickets]
    events = get_journal_for_tickets(ticket_ids)
    print(f"Событий в journal: {len(events)}")

    rows = collect_lifecycles(tickets, events)

    print()
    for row in rows:
        print(f"{'─' * 80}")
        print(f"Тикет      : {row['ticket_id']}")
        print(f"Открыт     : {row['start_at']}")
        print(f"Закрыт     : {row['end_at']}")
        print(f"Время жизни        : {_fmt_sec(row['время_жизни_сек'])} ({row['время_жизни_сек']}с)")
        print(f"Клиент ждёт саппорт: {_fmt_sec(row['ожидание_ответа_саппорта_сек'])} ({row['ожидание_ответа_саппорта_сек']}с)")
        print(f"Саппорт ждёт клиент: {_fmt_sec(row['ожидание_ответа_клиента_сек'])} ({row['ожидание_ответа_клиента_сек']}с)")
        print(f"Итераций           : {row['число_итераций']}")
        print(f"Lifecycle  : {row['lifecycle']}")
    print(f"{'─' * 80}")
    print(f"Итого тикетов: {len(rows)}")

    if not args.no_sheet:
        period_label = f"{args.date_from} — {args.date_to}"
        write_to_sheet(rows, period_label)


def run_all(args):
    print(f"Сбор lifecycle + передачи за {args.date_from} — {args.date_to}, тег: '{args.tag}' ...")
    tickets = get_closed_tickets(args.date_from, args.date_to, tag=args.tag)

    if not tickets:
        print("Тикетов не найдено.")
        sys.exit(0)

    print(f"Найдено тикетов: {len(tickets)}. Загружаем journal и передачи ...")
    ticket_ids = [int(t["id"]) for t in tickets]

    events = get_journal_for_tickets(ticket_ids)
    move_counts = get_move_counts_for_tickets(ticket_ids)

    move_map = {int(r["ticket_id"]): r for r in move_counts}

    rows = collect_lifecycles(tickets, events)
    for row in rows:
        mc = move_map.get(int(row["ticket_id"]), {})
        row["кол_во_передач"] = int(mc.get("count_move") or 0)
        row["кол_во_передач_вручную"] = int(mc.get("count_manual_move") or 0)
        row["lifecycle_history"] = row.pop("lifecycle")
        row["ticket_id"] = f"https://hp.beget.ru/ticket/{row['ticket_id']}"

    print(f"Итого тикетов: {len(rows)}")

    if not args.no_sheet:
        period_label = f"{args.date_from} — {args.date_to}"
        write_all(rows, period_label)


def run_cloud_stats(args):
    print(f"Сбор cloud-статистики за [{args.date_from}, {args.date_to}) ...")
    rows = get_cloud_ticket_stats(args.date_from, args.date_to)
    print(f"Получено месяцев: {len(rows)}")
    for r in rows:
        print(f"  {r['month']}: тикетов={r['count_ticket']}, перемещений={r['count_move']}")

    if not args.no_sheet:
        period_label = f"{args.date_from} — {args.date_to}"
        write_cloud_stats(rows, period_label)


def run_cloud_moves(args):
    print(f"Сбор тикетов с повторными перемещениями за [{args.date_from}, {args.date_to}) ...")
    rows = get_cloud_move_tickets(args.date_from, args.date_to)
    print(f"Найдено тикетов: {len(rows)}")
    if rows:
        total_move = sum(r["count_move"] for r in rows)
        total_auto = sum(r["count_autorouting"] for r in rows)
        print(f"Всего перемещений: {total_move}, из них autorouting: {total_auto}")

    if not args.no_sheet:
        period_label = f"{args.date_from} — {args.date_to}"
        write_cloud_moves(rows, period_label)


TASKS = {
    "lifecycle": run_lifecycle,
    "cloud-stats": run_cloud_stats,
    "cloud-moves": run_cloud_moves,
    "all": run_all,
}


def main():
    parser = argparse.ArgumentParser(description="Сбор статистики по тикетам")
    parser.add_argument("--task", required=True, choices=TASKS.keys(),
                        help="Что собирать: lifecycle | cloud-stats | cloud-moves")
    parser.add_argument("--from", dest="date_from", required=True, help="Дата начала YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", required=True, help="Дата конца YYYY-MM-DD")
    parser.add_argument("--tag", dest="tag", default="cloud_first",
                        help="Тег AI на клиентском посте (только для lifecycle, default: cloud_first)")
    parser.add_argument("--ticket", dest="ticket_id", type=int, default=None,
                        help="Конкретный ticket_id (только для lifecycle)")
    parser.add_argument("--no-sheet", dest="no_sheet", action="store_true",
                        help="Не писать в Google Sheets")
    args = parser.parse_args()

    TASKS[args.task](args)


if __name__ == "__main__":
    main()
