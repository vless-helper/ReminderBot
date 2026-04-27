def decline_months(months: int) -> str:
    """
    Возвращает правильное склонение слова "месяц" для числа месяцев
    """
    if 11 <= months % 100 <= 14:
        return "месяцев"
    
    last_digit = months % 10
    
    if last_digit == 1:
        return "месяц"
    elif 2 <= last_digit <= 4:
        return "месяца"
    else:
        return "месяцев"


def format_months(months: int, with_number: bool = True) -> str:
    """
    Форматирует число месяцев с правильным склонением
    Пример:
    """
    if with_number:
        return f"{months} {decline_months(months)}"
    return decline_months(months)


def format_price(price: int) -> str:
    """Форматирует цену с символом рубля"""
    return f"{price}₽"


def format_date(date) -> str:
    """Форматирует дату в readable формат"""
    if date:
        return date.strftime('%d.%m.%Y')
    return "не указано"