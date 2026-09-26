"""Private owner-only Telegram interface."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import logging
from collections.abc import Iterable
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from agent_session_dispatcher.attachments import (
    MAX_IMAGE_BYTES,
    AttachmentError,
    AttachmentStore,
    AttachmentTooLargeError,
    UnsupportedImageError,
)
from agent_session_dispatcher.config import Settings
from agent_session_dispatcher.domain import SavedSession
from agent_session_dispatcher.follower import FollowerSupervisor, SessionFollower
from agent_session_dispatcher.input_delivery import SafeInputSender
from agent_session_dispatcher.providers.codex import format_image_prompt
from agent_session_dispatcher.store import StateStore
from agent_session_dispatcher.tmux import TmuxClient, TmuxError

logger = logging.getLogger(__name__)

_DISPLAY_NAME_LIMIT = 64
_IMAGE_DOCUMENT_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
_IMAGE_DOCUMENT_SUFFIXES = {".jpeg", ".jpg", ".png", ".webp"}


def _chunks(text: str, limit: int = 3900) -> Iterable[str]:
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            yield remaining
            return
        split = remaining.rfind("\n", 0, limit)
        if split < limit // 2:
            split = limit
        yield remaining[:split]
        remaining = remaining[split:].lstrip("\n")


def _live_token(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def _normalize_display_name(value: str) -> str:
    normalized = " ".join(value.split())
    if not normalized:
        raise ValueError("Название не может быть пустым.")
    if len(normalized) > _DISPLAY_NAME_LIMIT:
        raise ValueError(f"Название должно быть не длиннее {_DISPLAY_NAME_LIMIT} символов.")
    return normalized


def _short_label(value: str, limit: int = 56) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1]}…"


def _session_identity(saved: SavedSession, *, heading: str = "Активная сессия") -> str:
    if saved.display_name:
        return f"{heading}: {saved.display_name}\nTmux: {saved.name}"
    return f"{heading}: {saved.name}"


def _is_image_document(mime_type: str | None, file_name: str | None) -> bool:
    normalized_mime = (mime_type or "").casefold()
    suffix = Path(file_name or "").suffix.casefold()
    if normalized_mime in _IMAGE_DOCUMENT_MIME_TYPES:
        return True
    return normalized_mime in {"", "application/octet-stream"} and suffix in (
        _IMAGE_DOCUMENT_SUFFIXES
    )


class TelegramApplication:
    def __init__(self, settings: Settings, store: StateStore, tmux: TmuxClient) -> None:
        self.settings = settings
        self.store = store
        self.tmux = tmux
        self.bot = Bot(token=settings.telegram_bot_token)
        self.dispatcher = Dispatcher()
        self.router = Router()
        self.sender = SafeInputSender(tmux)
        self.attachments = AttachmentStore(settings.attachments_dir)
        self._routing_lock = asyncio.Lock()
        self.follower = SessionFollower(
            store,
            tmux,
            self._send_agent_output,
            poll_interval=settings.poll_interval_seconds,
        )
        self.supervisor = FollowerSupervisor(store, self.follower)
        self._rename_target_id: int | None = None
        self._wire_handlers()
        self.dispatcher.include_router(self.router)

    def _message_allowed(self, message: Message) -> bool:
        return bool(
            message.chat.type == ChatType.PRIVATE
            and message.from_user is not None
            and message.from_user.id == self.settings.owner_user_id
        )

    def _callback_allowed(self, callback: CallbackQuery) -> bool:
        return bool(
            callback.from_user.id == self.settings.owner_user_id
            and callback.message is not None
            and callback.message.chat.type == ChatType.PRIVATE
        )

    @staticmethod
    def _main_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Сессии", callback_data="menu:sessions"),
                    InlineKeyboardButton(text="Добавить", callback_data="menu:add"),
                ],
                [
                    InlineKeyboardButton(text="Текущая", callback_data="menu:current"),
                    InlineKeyboardButton(text="Переименовать", callback_data="menu:rename"),
                ],
                [
                    InlineKeyboardButton(text="Отключиться", callback_data="menu:disconnect"),
                ],
            ]
        )

    def _wire_handlers(self) -> None:
        self.router.message.register(self._start, Command("start"))
        self.router.message.register(self._help, Command("help"))
        self.router.message.register(self._sessions_command, Command("sessions"))
        self.router.message.register(self._add_command, Command("add"))
        self.router.message.register(self._current_command, Command("current", "status"))
        self.router.message.register(self._rename_command, Command("rename"))
        self.router.message.register(self._cancel_command, Command("cancel"))
        self.router.message.register(self._disconnect_command, Command("disconnect"))
        self.router.message.register(self._remove_command, Command("remove"))
        self.router.callback_query.register(self._menu_callback, F.data.startswith("menu:"))
        self.router.callback_query.register(self._select_callback, F.data.startswith("select:"))
        self.router.callback_query.register(self._discover_callback, F.data.startswith("discover:"))
        self.router.callback_query.register(self._rename_callback, F.data.startswith("rename:"))
        self.router.message.register(self._photo_message, F.photo)
        self.router.message.register(self._document_message, F.document)
        self.router.message.register(self._text_message, F.text)
        self.router.message.register(self._unsupported_message)

    async def _start(self, message: Message) -> None:
        if not self._message_allowed(message):
            return
        self._rename_target_id = None
        await message.answer(
            "Диспетчер подключает этот приватный чат к выбранной tmux-сессии. "
            "Он не создаёт и не останавливает агентов.",
            reply_markup=self._main_keyboard(),
        )

    async def _help(self, message: Message) -> None:
        if not self._message_allowed(message):
            return
        self._rename_target_id = None
        await message.answer(
            "/sessions — сохранённые сессии\n"
            "/add [точное_имя] — добавить и выбрать сессию\n"
            "/current — текущее подключение\n"
            "/rename [название] — назвать активную карточку; «-» вернёт имя tmux\n"
            "/cancel — отменить ввод названия\n"
            "/disconnect — отключиться\n"
            "/remove <точное_имя> — удалить карточку\n\n"
            "Текст, фотографии и изображения JPEG/PNG/WebP отправляются только "
            "в выбранную online-сессию."
        )

    async def _sessions_command(self, message: Message) -> None:
        if self._message_allowed(message):
            self._rename_target_id = None
            await self._show_sessions(message)

    async def _add_command(self, message: Message) -> None:
        if not self._message_allowed(message):
            return
        self._rename_target_id = None
        text = message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            await self._add_exact(message, parts[1].strip())
            return
        await self._show_discovery(message)

    async def _current_command(self, message: Message) -> None:
        if self._message_allowed(message):
            self._rename_target_id = None
            await self._show_current(message)

    async def _rename_command(self, message: Message) -> None:
        if not self._message_allowed(message):
            return
        active = self.store.active_session()
        saved = self.store.get_session_by_name(active) if active is not None else None
        if saved is None:
            self._rename_target_id = None
            await message.answer("Сначала выберите сессию: /sessions или /add.")
            return
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2:
            await self._apply_display_name(message, saved.id, parts[1])
            return
        await self._begin_rename(message, saved)

    async def _cancel_command(self, message: Message) -> None:
        if not self._message_allowed(message):
            return
        was_pending = self._rename_target_id is not None
        self._rename_target_id = None
        await message.answer(
            "Переименование отменено." if was_pending else "Сейчас нет ожидающего переименования."
        )

    async def _disconnect_command(self, message: Message) -> None:
        if not self._message_allowed(message):
            return
        self._rename_target_id = None
        async with self._routing_lock:
            self.store.disconnect()
        await message.answer("Отключено. Обычные сообщения больше не пересылаются.")

    async def _remove_command(self, message: Message) -> None:
        if not self._message_allowed(message):
            return
        self._rename_target_id = None
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            await message.answer("Использование: /remove <точное_имя_tmux>")
            return
        saved = self.store.get_session_by_name(parts[1].strip())
        if saved is None:
            await message.answer("Такой сохранённой сессии нет.")
            return
        async with self._routing_lock:
            self.store.remove_session(saved.id)
        await message.answer(f"Карточка удалена: {saved.name}")

    async def _menu_callback(self, callback: CallbackQuery) -> None:
        if not self._callback_allowed(callback):
            await callback.answer()
            return
        action = (callback.data or "").partition(":")[2]
        await callback.answer()
        if not isinstance(callback.message, Message):
            return
        if action != "rename":
            self._rename_target_id = None
        if action == "sessions":
            await self._show_sessions(callback.message)
        elif action == "add":
            await self._show_discovery(callback.message)
        elif action == "current":
            await self._show_current(callback.message)
        elif action == "rename":
            active = self.store.active_session()
            saved = self.store.get_session_by_name(active) if active is not None else None
            if saved is None:
                await callback.message.answer("Сначала выберите сессию: /sessions или /add.")
            else:
                await self._begin_rename(callback.message, saved)
        elif action == "disconnect":
            async with self._routing_lock:
                self.store.disconnect()
            await callback.message.answer("Отключено.")

    async def _select_callback(self, callback: CallbackQuery) -> None:
        if not self._callback_allowed(callback):
            await callback.answer()
            return
        raw = (callback.data or "").partition(":")[2]
        if not raw.isdigit():
            await callback.answer("Некорректная карточка", show_alert=True)
            return
        saved = self.store.get_session(int(raw))
        if saved is None:
            await callback.answer("Карточка уже удалена", show_alert=True)
            return
        self._rename_target_id = None
        async with self._routing_lock:
            self.store.select_session(saved.name)
        online = await self.tmux.has_session(saved.name)
        await callback.answer("Подключено" if online else "Сессия сохранена, но offline")
        if not isinstance(callback.message, Message):
            return
        state = "online" if online else "offline"
        await callback.message.answer(
            f"{_session_identity(saved)}\nСостояние: {state}",
            reply_markup=self._rename_keyboard(saved),
        )

    async def _discover_callback(self, callback: CallbackQuery) -> None:
        if not self._callback_allowed(callback):
            await callback.answer()
            return
        token = (callback.data or "").partition(":")[2]
        try:
            names = await self.tmux.list_sessions()
        except TmuxError:
            await callback.answer("Tmux недоступен", show_alert=True)
            return
        matches = [name for name in names if _live_token(name) == token]
        if len(matches) != 1:
            await callback.answer("Список изменился. Откройте его повторно.", show_alert=True)
            return
        self._rename_target_id = None
        async with self._routing_lock:
            saved = self.store.add_session(matches[0])
            self.store.select_session(saved.name)
        await callback.answer("Добавлено и подключено")
        if not isinstance(callback.message, Message):
            return
        await callback.message.answer(
            _session_identity(saved), reply_markup=self._rename_keyboard(saved)
        )

    async def _rename_callback(self, callback: CallbackQuery) -> None:
        if not self._callback_allowed(callback):
            await callback.answer()
            return
        raw = (callback.data or "").partition(":")[2]
        if not raw.isdigit():
            await callback.answer("Некорректная карточка", show_alert=True)
            return
        saved = self.store.get_session(int(raw))
        if saved is None:
            await callback.answer("Карточка уже удалена", show_alert=True)
            return
        await callback.answer("Введите новое название")
        if isinstance(callback.message, Message):
            await self._begin_rename(callback.message, saved)

    async def _text_message(self, message: Message) -> None:
        if not self._message_allowed(message):
            return
        text = message.text or ""
        if text.startswith("/"):
            await message.answer("Неизвестная команда. Используйте /help.")
            return
        if self._rename_target_id is not None:
            await self._apply_display_name(message, self._rename_target_id, text)
            return
        async with self._routing_lock:
            active = self.store.active_session()
            if active is None:
                await message.answer("Сначала выберите сессию: /sessions или /add.")
                return
            result = await self.sender.send(active, text)
        if not result.delivered:
            await message.answer(result.message)

    async def _photo_message(self, message: Message) -> None:
        if not self._message_allowed(message) or not message.photo:
            return
        photo = message.photo[-1]
        await self._handle_image(
            message,
            file_id=photo.file_id,
            declared_size=photo.file_size,
            caption=message.caption or "",
        )

    async def _document_message(self, message: Message) -> None:
        if not self._message_allowed(message) or message.document is None:
            return
        document = message.document
        if not _is_image_document(document.mime_type, document.file_name):
            await message.answer(
                "Этот документ не является поддерживаемым изображением. "
                "Отправьте JPEG, PNG или WebP."
            )
            return
        await self._handle_image(
            message,
            file_id=document.file_id,
            declared_size=document.file_size,
            caption=message.caption or "",
        )

    async def _handle_image(
        self,
        message: Message,
        *,
        file_id: str,
        declared_size: int | None,
        caption: str,
    ) -> None:
        if self._rename_target_id is not None:
            await message.answer(
                "Сейчас ожидается название карточки. Изображение не отправлено агенту. "
                "Введите название или используйте /cancel."
            )
            return
        active = self.store.active_session()
        saved = self.store.get_session_by_name(active) if active is not None else None
        if saved is None:
            await message.answer("Сначала выберите сессию: /sessions или /add.")
            return
        assert active is not None
        try:
            image = await self.attachments.download_image(self.bot, file_id, declared_size)
        except AttachmentTooLargeError:
            await message.answer(
                f"Изображение слишком большое. Максимальный размер — "
                f"{MAX_IMAGE_BYTES // (1024 * 1024)} МБ."
            )
            return
        except UnsupportedImageError:
            await message.answer("Файл не распознан как JPEG, PNG или WebP.")
            return
        except AttachmentError:
            logger.warning("failed to store Telegram image", exc_info=True)
            await message.answer("Не удалось скачать изображение из Telegram. Повторите попытку.")
            return

        prompt = format_image_prompt(image.path, caption)
        async with self._routing_lock:
            if self.store.active_session() != active:
                result = None
            else:
                result = await self.sender.send(active, prompt)
        if result is None:
            await message.answer(
                "Активная сессия изменилась во время загрузки. Изображение не отправлено."
            )
        elif not result.delivered:
            await message.answer(result.message)
        else:
            await message.answer(f"Изображение передано агенту: {saved.label}.")

    async def _unsupported_message(self, message: Message) -> None:
        if not self._message_allowed(message):
            return
        await message.answer(
            "Этот тип сообщения пока не поддерживается. Отправьте текст, фотографию "
            "или изображение JPEG/PNG/WebP как документ."
        )

    async def _add_exact(self, message: Message, name: str) -> None:
        try:
            names = await self.tmux.list_sessions()
        except TmuxError:
            await message.answer("Не удалось получить список tmux-сессий.")
            return
        if name not in names:
            await message.answer("Точное имя не найдено в текущем tmux ls.")
            return
        async with self._routing_lock:
            saved = self.store.add_session(name)
            self.store.select_session(name)
        await message.answer(
            f"Добавлено и подключено: {saved.name}",
            reply_markup=self._rename_keyboard(saved),
        )

    async def _show_sessions(self, message: Message) -> None:
        saved = self.store.list_sessions()
        if not saved:
            await message.answer("Сохранённых сессий пока нет. Нажмите «Добавить».")
            return
        try:
            live = set(await self.tmux.list_sessions())
        except TmuxError:
            live = set()
        active = self.store.active_session()
        rows: list[list[InlineKeyboardButton]] = []
        for session in saved:
            marker = "●" if session.name in live else "○"
            selected = " ✓" if session.name == active else ""
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{marker} {_short_label(session.label)}{selected}",
                        callback_data=f"select:{session.id}",
                    )
                ]
            )
        await message.answer(
            "Выберите сохранённую tmux-сессию:\n● online · ○ offline",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def _show_discovery(self, message: Message) -> None:
        try:
            names = await self.tmux.list_sessions()
        except TmuxError:
            await message.answer("Не удалось получить список tmux-сессий.")
            return
        saved = {session.name for session in self.store.list_sessions()}
        available = [name for name in names if name not in saved]
        if not available:
            await message.answer("Новых tmux-сессий нет. Можно также использовать /add точное_имя.")
            return
        rows = [
            [
                InlineKeyboardButton(
                    text=name,
                    callback_data=f"discover:{_live_token(name)}",
                )
            ]
            for name in available
        ]
        await message.answer(
            "Добавить tmux-сессию:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        )

    async def _show_current(self, message: Message) -> None:
        active = self.store.active_session()
        if active is None:
            await message.answer("Активная сессия не выбрана.")
            return
        saved = self.store.get_session_by_name(active)
        if saved is None:
            await message.answer("Активная карточка не найдена. Выберите сессию повторно.")
            return
        online = await self.tmux.has_session(active)
        status = self.supervisor.status
        follow = status.state.value if status.session_name == active else "starting"
        detail = f"\n{html.escape(status.detail)}" if status.detail else ""
        await message.answer(
            f"<b>Название</b>: {html.escape(saved.label)}\n"
            f"<b>Tmux</b>: <code>{html.escape(saved.name)}</code>\n"
            f"tmux: {'online' if online else 'offline'}\n"
            f"вывод: {html.escape(follow)}{detail}",
            parse_mode=ParseMode.HTML,
            reply_markup=self._rename_keyboard(saved),
        )

    @staticmethod
    def _rename_keyboard(saved: SavedSession) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Переименовать",
                        callback_data=f"rename:{saved.id}",
                    )
                ]
            ]
        )

    async def _begin_rename(self, message: Message, saved: SavedSession) -> None:
        self._rename_target_id = saved.id
        await message.answer(
            "Отправьте новое понятное название для карточки:\n"
            f"{saved.name}\n\n"
            "Следующее обычное сообщение станет названием и не уйдёт агенту. "
            "Отправьте «-», чтобы вернуть техническое имя, или /cancel для отмены."
        )

    async def _apply_display_name(self, message: Message, session_id: int, raw_value: str) -> None:
        reset = raw_value.strip() == "-"
        try:
            display_name = None if reset else _normalize_display_name(raw_value)
        except ValueError as exc:
            await message.answer(f"{exc} Попробуйте ещё раз или используйте /cancel.")
            return
        async with self._routing_lock:
            saved = self.store.set_display_name(session_id, display_name)
        if saved is None:
            self._rename_target_id = None
            await message.answer("Карточка уже удалена. Переименование отменено.")
            return
        self._rename_target_id = None
        if saved.display_name:
            await message.answer(f"Название сохранено: {saved.display_name}\nTmux: {saved.name}")
        else:
            await message.answer(f"Псевдоним удалён. Название карточки: {saved.name}")

    async def _send_agent_output(self, session_name: str, text: str) -> None:
        if self.store.active_session() != session_name:
            return
        for chunk in _chunks(text):
            if self.store.active_session() != session_name:
                return
            await self.bot.send_message(
                chat_id=self.settings.owner_user_id,
                text=chunk,
                parse_mode=None,
            )

    async def run(self) -> None:
        await asyncio.to_thread(self.attachments.cleanup_expired)
        await self.bot.set_my_commands(
            [
                BotCommand(command="sessions", description="Сохранённые tmux-сессии"),
                BotCommand(command="add", description="Добавить tmux-сессию"),
                BotCommand(command="current", description="Текущее подключение"),
                BotCommand(command="rename", description="Переименовать карточку"),
                BotCommand(command="cancel", description="Отменить переименование"),
                BotCommand(command="disconnect", description="Отключиться"),
                BotCommand(command="help", description="Справка"),
            ]
        )
        await self.bot.delete_webhook(drop_pending_updates=True)
        supervisor_task = asyncio.create_task(self.supervisor.run(), name="follower-supervisor")
        try:
            await self.dispatcher.start_polling(self.bot)
        finally:
            supervisor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor_task
            await self.bot.session.close()
