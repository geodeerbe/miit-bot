import re
from datetime import date, datetime
from typing import Optional

import requests
from bs4 import BeautifulSoup

URL = "https://miit.ru/timetable/216019"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9",
}

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

WEEKDAYS_RU = [
    "Понедельник", "Вторник", "Среда",
    "Четверг", "Пятница", "Суббота", "Воскресенье",
]


def fetch_html() -> str:
    """Скачивает HTML страницы расписания."""
    resp = requests.get(URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def _parse_year(soup: BeautifulSoup) -> int:
    """Определяет учебный год из текста вида '1-й семестр 2026-2027'."""
    text = soup.get_text(" ", strip=True)
    m = re.search(r"(\d{4})\s*[-–]\s*(\d{4})", text)
    if m:
        return int(m.group(1))
    return date.today().year


def _parse_lesson_cell(cell) -> list[dict]:
    """
    Разбирает одну <td class='timetable__grid-day'>.
    Внутри может быть несколько пар (например, для разных подгрупп),
    разделённых <hr>.
    """
    lessons: list[dict] = []

    # Каждый блок "timetable__grid-day-lesson" — начало новой пары,
    # но внутри одной ячейки может быть 2 пары (для п/гр.1 и п/гр.2),
    # разделённые <hr class="mx-4">.
    #
    # Разобьём содержимое ячейки по <hr>.
    current: list = []
    for child in cell.children:
        if getattr(child, "name", None) == "hr":
            if current:
                lessons.append(_parse_lesson_block(current))
                current = []
        else:
            current.append(child)
    if current:
        lessons.append(_parse_lesson_block(current))

    return [l for l in lessons if l]


def _parse_lesson_block(nodes) -> Optional[dict]:
    """Разбирает один блок (до <hr>) — тип, предмет, преподаватель, аудитория, группы."""
    lesson = {
        "type": "",
        "subject": "",
        "teacher": "",
        "room": "",
        "groups": [],
    }

    for node in nodes:
        if getattr(node, "name", None) is None:
            continue

        cls = node.get("class") or []

        # Блок с типом и названием предмета
        if "timetable__grid-day-lesson" in cls:
            gray = node.find("span", class_="timetable__grid-text_gray")
            if gray:
                lesson["type"] = gray.get_text(strip=True)
                gray.extract()
            lesson["subject"] = node.get_text(" ", strip=True)

        # Преподаватель
        elif "icon-academic-cap" in cls:
            lesson["teacher"] = node.get_text(" ", strip=True)

        # Аудитория
        elif "icon-location" in cls:
            lesson["room"] = node.get_text(" ", strip=True)

        # Группы
        elif "icon-community" in cls:
            lesson["groups"].append(node.get_text(" ", strip=True))

        # На случай, если элементы вложены (BeautifulSoup иногда даёт div-обёртки)
        else:
            # ищем вложенные ссылки/спаны нужных классов
            for sub in node.find_all(True):
                sub_cls = sub.get("class") or []
                if "timetable__grid-day-lesson" in sub_cls and not lesson["subject"]:
                    gray = sub.find("span", class_="timetable__grid-text_gray")
                    if gray:
                        lesson["type"] = gray.get_text(strip=True)
                        gray.extract()
                    lesson["subject"] = sub.get_text(" ", strip=True)
                elif "icon-academic-cap" in sub_cls and not lesson["teacher"]:
                    lesson["teacher"] = sub.get_text(" ", strip=True)
                elif "icon-location" in sub_cls and not lesson["room"]:
                    lesson["room"] = sub.get_text(" ", strip=True)
                elif "icon-community" in sub_cls:
                    g = sub.get_text(" ", strip=True)
                    if g and g not in lesson["groups"]:
                        lesson["groups"].append(g)

    if not lesson["subject"] and not lesson["type"]:
        return None
    return lesson


def _parse_table(table) -> dict[date, list[dict]]:
    """
    Разбирает одну <table class="table timetable__grid">.
    Возвращает {дата: [ {number, time, type, subject, teacher, room, groups}, ... ]}.
    """
    result: dict[date, list[dict]] = {}

    rows = table.find_all("tr")
    if not rows:
        return result

    # --- Шапка: вытаскиваем даты по колонкам ---
    header_row = rows[0]
    ths = header_row.find_all("th")
    col_dates: list[Optional[date]] = []
    # Первая колонка — пустая (там номер пары)
    for th in ths[1:]:
        # Внутри th: "Понедельник <small>28 сентября</small>"
        text = th.get_text(" ", strip=True)
        m = re.search(
            r"(Понедельник|Вторник|Среда|Четверг|Пятница|Суббота|Воскресенье)"
            r"\s+(\d{1,2})\s+(\w+)",
            text,
            re.IGNORECASE,
        )
        if m:
            day = int(m.group(2))
            month = MONTHS_RU.get(m.group(3).lower())
            if month:
                year = _parse_year(table.find_parent() or table)
                # Если месяц январь–июль — это второй год учебного года
                y = year if month >= 8 else year + 1
                col_dates.append(date(y, month, day))
            else:
                col_dates.append(None)
        else:
            col_dates.append(None)

    # --- Тело таблицы: строки по парам ---
    for row in rows[1:]:
        tds = row.find_all("td", recursive=False)
        if not tds:
            continue

        # Первая ячейка — "N пара" + время
        first = tds[0].get_text(" ", strip=True)
        m = re.search(r"(\d+)\s*пара\s+(\d{2}:\d{2})\s*[—\-–]\s*(\d{2}:\d{2})", first)
        if m:
            pair_number = int(m.group(1))
            pair_time = f"{m.group(2)} — {m.group(3)}"
        else:
            m2 = re.search(r"(\d+)\s*пара", first)
            pair_number = int(m2.group(1)) if m2 else 0
            pair_time = ""

        for idx, td in enumerate(tds[1:]):
            if idx >= len(col_dates):
                break
            d = col_dates[idx]
            if d is None:
                continue

            lessons = _parse_lesson_cell(td)
            if not lessons:
                continue

            for lesson in lessons:
                lesson["number"] = pair_number
                lesson["time"] = pair_time
                result.setdefault(d, []).append(lesson)

    return result


def parse_schedule(html: str) -> dict[date, list[dict]]:
    """Главная функция: HTML → {дата: [пары]}."""
    soup = BeautifulSoup(html, "html.parser")
    schedule: dict[date, list[dict]] = {}

    for table in soup.find_all("table", class_="timetable__grid"):
        # Определяем год по всей странице
        year = _parse_year(soup)
        rows = table.find_all("tr")
        if not rows:
            continue

        # Шапка
        col_dates: list[Optional[date]] = []
        for th in rows[0].find_all("th")[1:]:
            text = th.get_text(" ", strip=True)
            m = re.search(
                r"(Понедельник|Вторник|Среда|Четверг|Пятница|Суббота|Воскресенье)"
                r"\s+(\d{1,2})\s+(\w+)",
                text,
                re.IGNORECASE,
            )
            if m:
                day = int(m.group(2))
                month = MONTHS_RU.get(m.group(3).lower())
                if month:
                    y = year if month >= 8 else year + 1
                    col_dates.append(date(y, month, day))
                else:
                    col_dates.append(None)
            else:
                col_dates.append(None)

        # Тело
        for row in rows[1:]:
            tds = row.find_all("td", recursive=False)
            if not tds:
                continue

            first = tds[0].get_text(" ", strip=True)
            m = re.search(
                r"(\d+)\s*пара\s+(\d{2}:\d{2})\s*[—\-–]\s*(\d{2}:\d{2})",
                first,
            )
            if m:
                pair_number = int(m.group(1))
                pair_time = f"{m.group(2)} — {m.group(3)}"
            else:
                m2 = re.search(r"(\d+)\s*пара", first)
                pair_number = int(m2.group(1)) if m2 else 0
                pair_time = ""

            for idx, td in enumerate(tds[1:]):
                if idx >= len(col_dates):
                    break
                d = col_dates[idx]
                if d is None:
                    continue

                lessons = _parse_lesson_cell(td)
                for lesson in lessons:
                    lesson["number"] = pair_number
                    lesson["time"] = pair_time
                    schedule.setdefault(d, []).append(lesson)

    return schedule


if __name__ == "__main__":
    html = fetch_html()
    schedule = parse_schedule(html)
    print(f"Найдено дней: {len(schedule)}")
    for d in sorted(schedule):
        print(f"\n📅 {d.strftime('%d.%m.%Y')} — {WEEKDAYS_RU[d.weekday()]}")
        for l in schedule[d]:
            print(
                f"  Пара {l['number']}: {l['subject']} "
                f"({l['type']}) | {l['teacher']} | {l['room']} | "
                f"{', '.join(l['groups']) or '—'} | {l['time']}"
            )