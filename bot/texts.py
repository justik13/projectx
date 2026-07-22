# bot/texts.py
#
# Единый загрузчик текстов.
#
# Все тексты хранятся в:
# - bot/texts_data/user_texts.py
# - bot/texts_data/admin_texts.py
#
# Этот файл:
# 1. Загружает оба словаря.
# 2. Проверяет, что нет дублирующихся ключей.
# 3. Проверяет, что все ключи являются валидными Python identifier.
# 4. Публикует ключи как атрибуты модуля.
#
# Использование остаётся прежним:
#
#   from bot import texts
#   texts.HUB_HEADER.format(name="Test")
#
# Или:
#
#   from bot.texts import get_text
#   get_text("HUB_HEADER")

import logging
from importlib import reload
from typing import Any

from bot.texts_data import admin_texts as _admin_texts_module
from bot.texts_data import user_texts as _user_texts_module

logger = logging.getLogger(__name__)


def _validate_key(key: Any) -> None:
    if not isinstance(key, str):
        raise RuntimeError(
            f"Text key must be string, got {type(key).__name__}: {key!r}"
        )

    if not key.isidentifier():
        raise RuntimeError(
            f"Text key must be a valid Python identifier: {key!r}"
        )


def _merge_texts() -> dict[str, Any]:
    user_texts = dict(_user_texts_module.TEXTS)
    admin_texts = dict(_admin_texts_module.TEXTS)

    duplicates = sorted(set(user_texts.keys()) & set(admin_texts.keys()))
    if duplicates:
        raise RuntimeError(
            "Duplicate text keys between user_texts and admin_texts: "
            f"{duplicates}"
        )

    merged: dict[str, Any] = {}

    for source_name, source in (
        ("user_texts", user_texts),
        ("admin_texts", admin_texts),
    ):
        for key, value in source.items():
            _validate_key(key)

            if key in merged:
                raise RuntimeError(
                    f"Duplicate text key {key!r} in {source_name}"
                )

            merged[key] = value

    return merged


_TEXTS = _merge_texts()

# Публикуем все тексты как атрибуты модуля.
# Это сохраняет совместимость со старым bot/texts.py.
globals().update(_TEXTS)

__all__ = list(_TEXTS.keys()) + [
    "get_text",
    "get_all_text_keys",
    "reload_texts",
]


def get_text(key: str, default: Any = None) -> Any:
    """
    Безопасно получить текст по ключу.
    """
    return _TEXTS.get(key, default)


def get_all_text_keys() -> list[str]:
    """
    Возвращает список всех загруженных текстовых ключей.
    """
    return list(_TEXTS.keys())


def reload_texts() -> None:
    """
    Перезагружает текстовые модули.
    Полезно при разработке, чтобы не перезапускать бота.
    """
    global _TEXTS

    reload(_user_texts_module)
    reload(_admin_texts_module)

    _TEXTS = _merge_texts()
    globals().update(_TEXTS)

    logger.info(
        "Texts reloaded successfully: %s keys",
        len(_TEXTS),
    )