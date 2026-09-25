import asyncio
import logging
import sys
import time
from datetime import date, datetime, timedelta
from os import getenv

from aiogram import Bot, Dispatcher, F, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from parser import fetch_html, parse_schedule

TOKEN = "PASTE_HERE"
WEEKDAYS_RU = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]

dp = Dispatcher()
_cache: dict = {"ts": 0, "schedule": {}}
CACHE_TTL = 60 * 15  # 15 минут


# ---------- Клавиатуры ----------
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Сегодня"),
                KeyboardButton(text="Завтра"),
                KeyboardButton(text="Неделя"),
            ],
        ],
        resize_keyboard=True,
    )


def day_nav_kb(offset: int) -> InlineKeyboardMarkup:
    target = date.today() + timedelta(days=offset)
    label = target.strftime("%d.%m.%Y")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️", callback_data=f"day:{offset - 1}"),
                InlineKeyboardButton(text=label, callback_data="noop"),
                InlineKeyboardButton(text="➡️", callback_data=f"day:{offset + 1}"),
            ],
        ]
    )


def week_nav_kb(offset: int) -> InlineKeyboardMarkup:
    start, end = week_bounds(offset)
    label = f"{start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️", callback_data=f"week:{offset - 1}"),
                InlineKeyboardButton(text=label, callback_data="noop"),
                InlineKeyboardButton(text="➡️", callback_data=f"week:{offset + 1}"),
            ],
        ]
    )


# ---------- Кэш ----------
async def get_schedule() -> dict[date, list[dict]]:
    now = time.time()
    if _cache["schedule"] and (now - _cache["ts"] < CACHE_TTL):
        return _cache["schedule"]
    html = fetch_html()
    schedule = parse_schedule(html)
    _cache["schedule"] = schedule
    _cache["ts"] = now
    return schedule


# ---------- Форматирование ----------
def format_day(target: date, lessons: list[dict]) -> str:
    weekday = WEEKDAYS_RU[target.weekday()]
    header = f"💸 {target.strftime('%d.%m.%Y')} - {weekday}"

    if not lessons:
        return f"{header}\n\nВ этот день пар нет."

    # Сортируем по номеру пары
    lessons = sorted(lessons, key=lambda x: x.get("number", 0))

    lines = [header, f"В этот день у тебя {len(lessons)} пар!", ""]
    for lesson in lessons:
        title = lesson["subject"] or lesson["type"] or "Пара"
        # Добавляем тип в скобках, если он есть и ещё не в названии
        if lesson["type"] and lesson["type"].lower() not in title.lower():
            title = f"{title} ({lesson['type']})"

        lines.append(f"Пара {lesson.get('number', '?')}: {title}")
        if lesson["teacher"]:
            lines.append(f" ┣ Преподаватель: {lesson['teacher']}")
        if lesson["room"]:
            lines.append(f" ┣ Аудитория: {lesson['room']}")
        if lesson["groups"]:
            lines.append(f" ┣ Группы: {', '.join(lesson['groups'])}")
        if lesson["time"]:
            lines.append(f" ┗ Время: {lesson['time']}")
        lines.append("")
    lines.append(f"Обновлено: {datetime.now().strftime('%H:%M')}")
    return "\n".join(lines).rstrip()


def format_week(schedule: dict[date, list[dict]], start: date, end: date) -> str:
    d = start
    total = 0
    blocks = []

    while d <= end:
        lessons = schedule.get(d)
        if lessons:
            total += len(lessons)
            lines = [f"{d.strftime('%d.%m.%Y')} - {WEEKDAYS_RU[d.weekday()]}"]
            for lesson in sorted(lessons, key=lambda x: x.get("number", 0)):
                title = lesson["subject"] or lesson["type"] or "Пара"
                room = lesson["room"] or "*"
                # Короткий формат: Пара N: Предмет | Тип | Аудитория
                type_str = lesson["type"] or ""
                lines.append(f"Пара {lesson.get('number', '?')}: {title}")
                lines.append(f"┗ {type_str} | {room}" if type_str else f"┗ {room}")
            blocks.append("\n".join(lines))
        d += timedelta(days=1)

    header = f"⭐ {start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}\n"
    header += f"На этой неделе у тебя {total} пар"
    body = "\n\n".join(blocks) if blocks else "На этой неделе пар нет."
    footer = f"\n\nОбновлено: {datetime.now().strftime('%H:%M')}"
    return f"{header}\n\n{body}{footer}"


def week_bounds(offset: int = 0) -> tuple[date, date]:
    today = date.today()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
    friday = monday + timedelta(days=4)
    return monday, friday


async def send_long(message: Message, text: str) -> None:
    max_len = 4000
    for i in range(0, len(text), max_len):
        await message.answer(text[i:i + max_len])
        await asyncio.sleep(0.4)


# ---------- Хендлеры ----------
@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        f"Привет, {html.bold(message.from_user.full_name)}!\nВыбери период 👇",
        reply_markup=main_menu(),
    )


@dp.message(F.text.in_({"Сегодня", "Завтра"}))
async def on_day_button(message: Message) -> None:
    offset = 0 if message.text == "Сегодня" else 1
    try:
        schedule = await get_schedule()
    except Exception as e:
        await message.answer(f"Ошибка загрузки: {e}")
        return
    target = date.today() + timedelta(days=offset)
    text = format_day(target, schedule.get(target, []))
    await message.answer(text, reply_markup=day_nav_kb(offset))


@dp.message(F.text == "Неделя")
async def on_week_button(message: Message) -> None:
    try:
        schedule = await get_schedule()
    except Exception as e:
        await message.answer(f"Ошибка загрузки: {e}")
        return
    start, end = week_bounds(0)
    text = format_week(schedule, start, end)
    await message.answer(text, reply_markup=week_nav_kb(0))


@dp.callback_query(F.data == "noop")
async def on_noop(cb: CallbackQuery) -> None:
    await cb.answer()


@dp.callback_query(F.data.startswith("day:"))
async def on_day_nav(cb: CallbackQuery) -> None:
    await cb.answer()
    offset = int(cb.data.split(":")[1])
    try:
        schedule = await get_schedule()
    except Exception as e:
        await cb.message.answer(f"Ошибка: {e}")
        return
    target = date.today() + timedelta(days=offset)
    text = format_day(target, schedule.get(target, []))
    try:
        await cb.message.edit_text(text, reply_markup=day_nav_kb(offset))
    except Exception:
        pass


@dp.callback_query(F.data.startswith("week:"))
async def on_week_nav(cb: CallbackQuery) -> None:
    await cb.answer()
    offset = int(cb.data.split(":")[1])
    try:
        schedule = await get_schedule()
    except Exception as e:
        await cb.message.answer(f"Ошибка: {e}")
        return
    start, end = week_bounds(offset)
    text = format_week(schedule, start, end)
    try:
        await cb.message.edit_text(text, reply_markup=week_nav_kb(offset))
    except Exception:
        pass


async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
