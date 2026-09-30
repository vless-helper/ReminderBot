def decline_months(months: int) -> str:
    """Правильное склонение слова «месяц»: 1 месяц / 2 месяца / 5 месяцев."""
    if 11 <= months % 100 <= 14:
        return "месяцев"

    last_digit = months % 10
    if last_digit == 1:
        return "месяц"
    if 2 <= last_digit <= 4:
        return "месяца"
    return "месяцев"


def format_months(months: int, with_number: bool = True) -> str:
    return f"{months} {decline_months(months)}" if with_number else decline_months(months)


def format_price(price: int) -> str:
    return f"{price}₽"


def format_date(date) -> str:
    return date.strftime("%d.%m.%Y") if date else "не указано"


def username_or_id(user) -> str:
    """Подпись пользователя: @username, а если его нет — telegram_id."""
    if user is None:
        return "неизвестно"
    return f"@{user.username}" if user.username else f"id {user.telegram_id}"
