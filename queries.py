from db.mysql_ssh import run_mysql_query

DB_HELPDESK = "DB_HELPDESK"

CHUNK_SIZE = 500


def _ph(values):
    return ",".join(["%s"] * len(values))


def _chunks(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def get_closed_tickets(date_from: str, date_to: str, tag: str = None) -> list:
    """Тикеты, созданные в диапазоне дат и закрытые (status_id 3 или 4).

    Если передан tag — возвращает только тикеты, у которых хотя бы один
    клиентский пост содержит этот тег в post.metadata->'$.tags'.
    """
    if tag:
        # Тег AI хранится в post_tag_ai (post_id, tag_slug).
        # Берём первый пост тикета и проверяем его тег в post_tag_ai.
        sql = """
            SELECT t.id, t.status_id, t.created_at, t.updated_at
            FROM ticket t
            JOIN post p ON p.ticket_id = t.id
              AND p.id = (SELECT MIN(p2.id) FROM post p2 WHERE p2.ticket_id = t.id)
            JOIN post_tag_ai pta ON pta.post_id = p.id AND pta.tag_slug = %s
            WHERE DATE(t.created_at) BETWEEN %s AND %s
              AND t.status_id IN (3, 4)
            ORDER BY t.created_at
        """
        return run_mysql_query(DB_HELPDESK, sql, (tag, date_from, date_to)) or []
    sql = """
        SELECT id, status_id, created_at, updated_at
        FROM ticket
        WHERE DATE(created_at) BETWEEN %s AND %s
          AND status_id IN (3, 4)
        ORDER BY created_at
    """
    return run_mysql_query(DB_HELPDESK, sql, (date_from, date_to)) or []


def get_journal_for_tickets(ticket_ids: list) -> list:
    """Все события assignee / move / status из journal для списка тикетов."""
    if not ticket_ids:
        return []
    result = []
    for chunk in _chunks(ticket_ids, CHUNK_SIZE):
        sql = f"""
            SELECT id, ticket_id, author, property, old_value, new_value, created_at
            FROM journal
            WHERE ticket_id IN ({_ph(chunk)})
              AND (
                property = 'assignee'
                OR property = 'move'
                OR property = 'status'
              )
            ORDER BY ticket_id, created_at, id
        """
        result.extend(run_mysql_query(DB_HELPDESK, sql, tuple(chunk)) or [])
    return result


def get_move_counts_for_tickets(ticket_ids: list) -> list:
    """Кол-во передач на Cloud:Вторая линия на тикет: всего и вручную (не autorouting)."""
    if not ticket_ids:
        return []
    result = []
    for chunk in _chunks(ticket_ids, CHUNK_SIZE):
        sql = f"""
            SELECT
                t.id AS ticket_id,
                COUNT(j.id) AS count_move,
                COUNT(j.id) - SUM(CASE
                    WHEN (j.author = 'VM support' AND j.employee_id = 716)
                      OR (j.author = 'Сотрудник Для-Скрипта-Клюкова А.Е.' AND j.employee_id = 842)
                    THEN 1 ELSE 0
                END) AS count_manual_move
            FROM ticket t
            LEFT JOIN journal j
                ON j.ticket_id = t.id
                AND j.property = 'move'
                AND j.new_value = 'Cloud:Вторая линия'
            WHERE t.id IN ({_ph(chunk)})
            GROUP BY t.id
        """
        result.extend(run_mysql_query(DB_HELPDESK, sql, tuple(chunk)) or [])
    return result


def get_post_counts_for_tickets(ticket_ids: list) -> list:
    """Кол-во постов пользователя и саппорта на тикет."""
    if not ticket_ids:
        return []
    result = []
    for chunk in _chunks(ticket_ids, CHUNK_SIZE):
        sql = f"""
            SELECT
                ticket_id,
                SUM(CASE WHEN employee_id IS NULL THEN 1 ELSE 0 END) AS число_постов_пользователя,
                SUM(CASE WHEN employee_id IS NOT NULL THEN 1 ELSE 0 END) AS число_постов_саппорта
            FROM post
            WHERE ticket_id IN ({_ph(chunk)})
            GROUP BY ticket_id
        """
        result.extend(run_mysql_query(DB_HELPDESK, sql, tuple(chunk)) or [])
    return result


def get_cloud_ticket_stats(date_from: str, date_to: str) -> list:
    """Кол-во cloud-тикетов и перемещений на Вторую линию, сгруппированных по месяцам."""
    sql = """
        SELECT
            DATE_FORMAT(t.created_at, '%%Y-%%m') AS month,
            COUNT(DISTINCT p.ticket_id) AS count_ticket,
            COUNT(DISTINCT CASE WHEN j.ticket_id IS NOT NULL THEN p.ticket_id END) AS count_move
        FROM ticket t
        JOIN post p ON p.ticket_id = t.id
        JOIN post_tag_ai ai ON ai.post_id = p.id AND ai.tag_slug = 'cloud_first'
        LEFT JOIN journal j
            ON j.ticket_id = t.id
            AND j.property = 'move'
            AND j.new_value = 'Cloud:Вторая линия'
        WHERE t.created_at >= %s AND t.created_at < %s
        GROUP BY DATE_FORMAT(t.created_at, '%%Y-%%m')
        ORDER BY month
    """
    return run_mysql_query(DB_HELPDESK, sql, (date_from, date_to)) or []


def get_cloud_move_tickets(date_from: str, date_to: str) -> list:
    """Тикеты с более чем одним перемещением на Cloud:Вторая линия."""
    sql = """
        SELECT
            CONCAT('https://hp.beget.ru/ticket/', t.id) AS ticket_id,
            t.created_at AS created_at,
            COUNT(j.id) AS count_move,
            SUM(CASE
                WHEN j.author = 'VM support' AND j.employee_id = 716
                THEN 1 ELSE 0
            END) AS count_autorouting
        FROM ticket t
        JOIN post p ON p.ticket_id = t.id
        JOIN post_tag_ai ai ON ai.post_id = p.id AND ai.tag_slug = 'cloud_first'
        LEFT JOIN journal j
            ON j.ticket_id = t.id
            AND j.property = 'move'
            AND j.new_value = 'Cloud:Вторая линия'
        WHERE t.created_at >= %s AND t.created_at < %s
        GROUP BY t.id, t.created_at
        HAVING count_move >= 1
        ORDER BY t.created_at
    """
    return run_mysql_query(DB_HELPDESK, sql, (date_from, date_to)) or []
