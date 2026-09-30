"""Единый источник правды по ценам.

Раньше цена считалась в трёх местах (keyboards, admin, crud) по-разному:
клавиатура показывала сумму со скидкой за 3/6/12 месяцев, а обработчик
начислял без скидки, плюс calculate_monthly_price возвращал coroutine.
Теперь считаем только здесь и всегда одинаково.
"""

from dataclasses import dataclass

from bot.config import config


@dataclass(frozen=True)
class PriceBreakdown:
    """Разложение цены — показываем пользователю, из чего сложилась сумма."""

    months: int
    configs_count: int
    base_price: int
    subtotal: int
    bulk_discount: int
    period_discount: int
    total: int

    def lines(self) -> list[str]:
        out = [f"Конфигов: {self.configs_count} × {self.base_price}₽ = {self.subtotal}₽/мес"]
        if self.bulk_discount < 100:
            out.append(f"Скидка за {config.BULK_THRESHOLD}+ конфигов: −{100 - self.bulk_discount}%")
        if self.period_discount < 100:
            out.append(f"Срок {self.months} мес. × {self.period_discount}%")
        return out


def monthly_price(configs_count: int) -> int:
    """Сколько платит пользователь в месяц за текущий набор конфигов."""
    subtotal = config.BASE_PRICE * max(configs_count, 0)
    if configs_count >= config.BULK_THRESHOLD:
        subtotal = int(subtotal * config.BULK_DISCOUNT / 100)
    return max(subtotal, config.BASE_PRICE if configs_count else 0)


def subscription_price(months: int, configs_count: int) -> PriceBreakdown:
    """Полная цена подписки/продления на N месяцев.

    Для первой подписки конфигов ещё нет, поэтому configs_count = 1 (первый
    защищённый конфиг создаётся сразу после оплаты).
    """
    if not config.is_allowed_period(months):
        raise ValueError(f"Недопустимый срок: {months} месяцев")

    effective_configs = max(configs_count, 1)
    base = config.BASE_PRICE
    subtotal = base * effective_configs

    bulk_discount = config.BULK_DISCOUNT if effective_configs >= config.BULK_THRESHOLD else 100
    after_bulk = int(subtotal * bulk_discount / 100)

    period_discount = config.discount_for_months(months)
    total = int(after_bulk * months * period_discount / 100)

    return PriceBreakdown(
        months=months,
        configs_count=effective_configs,
        base_price=base,
        subtotal=subtotal,
        bulk_discount=bulk_discount,
        period_discount=period_discount,
        total=max(total, 1),
    )


def extra_config_price(days_left: int) -> tuple[int, str]:
    """Цена докупки конфига — пропорционально остатку оплаченного периода."""
    if days_left <= 0:
        return config.BASE_PRICE, "Оплата за полный месяц."

    ratio = min(days_left, config.STANDARD_MONTH_DAYS) / config.STANDARD_MONTH_DAYS
    price = max(1, int(config.BASE_PRICE * ratio))
    return price, (
        f"До следующего платежа осталось {days_left} дн.\n"
        f"Плата за новый конфиг — {price}₽ (пропорционально остатку периода).\n"
        f"С следующего продления будет взиматься полная стоимость."
    )
