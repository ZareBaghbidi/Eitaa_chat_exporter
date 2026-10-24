"""Command-line interface for the Eitaa HTML exporter."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Sequence
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from eitaa_cli import EitaaClient
from eitaa_cli.errors import (
    AuthenticationRequired,
    EitaaError,
    EitaaRPCError,
    OtpError,
    PeerResolutionError,
)

from . import __version__
from .exporter import Exporter, ExportOptions
from .models import ExportStats

COMMANDS = {"export", "login", "chats", "version"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eitaa-export",
        description="ذخیرهٔ کامل کانال، گروه یا گفت‌وگوی ایتا به شکل خروجی HTML تلگرام",
    )
    parser.add_argument("--version", action="version", version=f"eitaa-export {__version__}")
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    export = subparsers.add_parser(
        "export",
        help="برون‌ریزی یک کانال/گروه/گفت‌وگو",
        description="تمام تاریخچه و رسانه‌های یک کانال، گروه یا شخص را ذخیره می‌کند.",
    )
    export.add_argument("peer", help="لینک، @username، یا شناسهٔ طرف مقابل")
    export.add_argument("-o", "--output", help="پوشهٔ خروجی (پیش‌فرض: export_<نام>)")
    export.add_argument("--profile", help="نام پروفایل ذخیره‌شده در eitaa-cli")
    export.add_argument(
        "--join",
        action="store_true",
        help="در صورت نیاز، با لینک عضویت به گروه/کانال بپیوند",
    )
    export.add_argument("--no-media", action="store_true", help="فقط متن؛ رسانه‌ها دانلود نشوند")
    export.add_argument("--no-stickers", action="store_true", help="استیکرها دانلود نشوند")
    export.add_argument("--limit", type=int, help="فقط N پیام آخر")
    export.add_argument("--since", help="از تاریخ (YYYY-MM-DD یا ISO 8601)")
    export.add_argument("--until", help="تا تاریخ (YYYY-MM-DD یا ISO 8601)")
    export.add_argument(
        "--timezone",
        default="Asia/Tehran",
        help="منطقهٔ زمانی خروجی (پیش‌فرض: Asia/Tehran)",
    )
    export.add_argument(
        "--no-created",
        action="store_true",
        help="پیام ساخت کانال/گروه به ابتدای خروجی اضافه نشود",
    )
    export.add_argument("--page-size", type=int, default=100, help="اندازهٔ هر صفحهٔ دریافت")
    export.add_argument("-q", "--quiet", action="store_true", help="نمایش ندادن پیشرفت")

    login = subparsers.add_parser(
        "login",
        help="ورود با شمارهٔ تلفن و کد یک‌بارمصرف",
        description="با حساب ایتای خود وارد شوید تا خروجی بتواند تاریخچه را بخواند.",
    )
    login.add_argument("phone", nargs="?", help="شمارهٔ تلفن، مثلاً +989121234567")
    login.add_argument("--profile", help="نام پروفایل برای ذخیرهٔ نشست")

    chats = subparsers.add_parser(
        "chats",
        help="نمایش گفت‌وگوها و شناسه‌های قابل استفاده",
        description="فهرست گفت‌وگوها، کانال‌ها و گروه‌ها را با شناسهٔ قابل استفاده نشان می‌دهد.",
    )
    chats.add_argument("--limit", type=int, default=50, help="تعداد گفت‌وگو (پیش‌فرض: 50)")
    chats.add_argument("--query", help="جست‌وجو در نام/شناسه")
    chats.add_argument("--profile", help="نام پروفایل")

    subparsers.add_parser("version", help="نمایش نسخه")
    return parser


def _client_options(args: argparse.Namespace) -> dict[str, Any]:
    options: dict[str, Any] = {}
    profile = getattr(args, "profile", None)
    if profile:
        options["profile"] = profile
    return options


def _parse_moment(value: str | None, tz: ZoneInfo, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            day = datetime.strptime(text, "%Y-%m-%d").date()
            return datetime.combine(
                day,
                time(23, 59, 59) if end_of_day else time(0, 0, 0),
                tzinfo=tz,
            )
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"تاریخ نامعتبر است: {value}") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=tz)
    return moment


def _default_output(peer: str) -> Path:
    cleaned = peer.strip()
    cleaned = re.sub(r"^https?://(?:www\.)?eitaa\.(?:com|ir)/", "", cleaned)
    cleaned = cleaned.lstrip("@").strip("/")
    if not cleaned:
        cleaned = "eitaa"
    if ":" in cleaned and not re.search(r"[^\w:.-]", cleaned):
        cleaned = cleaned.replace(":", "_")
    cleaned = re.sub(r"[^\w\u0600-\u06FF.-]+", "_", cleaned)
    return Path(f"export_{cleaned[:60] or 'eitaa'}")


async def _run_login(args: argparse.Namespace) -> int:
    phone = args.phone
    if not phone:
        phone = input("شمارهٔ تلفن (مثلاً +989121234567): ").strip()
    if not phone:
        print("شمارهٔ تلفن لازم است.", file=sys.stderr)
        return 2
    client = await EitaaClient.create(require_auth=False, **_client_options(args))
    async with client:
        challenge = await client.auth.request_code(phone)
        delivery = getattr(challenge.delivery, "value", challenge.delivery)
        print(f"کد تأیید از طریق {delivery} ارسال شد.", file=sys.stderr)
        if challenge.next_delivery:
            print(f"روش جایگزین: {challenge.next_delivery}", file=sys.stderr)
        code = input("کد تأیید: ").strip()
        if not code:
            print("کد تأیید لازم است.", file=sys.stderr)
            return 2
        await client.auth.sign_in(
            challenge.phone_number,
            challenge.phone_code_hash,
            code,
            profile_name=args.profile,
        )
    print("ورود با موفقیت انجام شد. اکنون می‌توانید export بگیرید.")
    return 0


async def _run_chats(args: argparse.Namespace) -> int:
    client = await EitaaClient.create(require_auth=True, **_client_options(args))
    async with client:
        result = await client.dialogs.list(max(1, args.limit), query=args.query)
    users: dict[int, dict[str, Any]] = {}
    chats: dict[int, dict[str, Any]] = {}
    for item in result.get("users", []):
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            users[item["id"]] = dict(item)
    for item in result.get("chats", []):
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            chats[item["id"]] = dict(item)
    rows: list[tuple[str, str, str, str]] = []
    for dialog in result.get("dialogs", []):
        peer = dialog.get("peer") if isinstance(dialog, dict) else None
        if not isinstance(peer, dict):
            continue
        predicate = peer.get("_")
        raw_identifier = (
            peer.get("user_id")
            if predicate == "peerUser"
            else peer.get("chat_id")
            if predicate == "peerChat"
            else peer.get("channel_id")
        )
        identifier = raw_identifier if isinstance(raw_identifier, int) else None
        if identifier is None:
            continue
        if predicate == "peerUser":
            entity = users.get(identifier)
            kind = "private"
            reference = _reference("user", entity, identifier)
            title = _display_name(entity)
        elif predicate == "peerChat":
            entity = chats.get(identifier)
            kind = "group"
            reference = f"chat:{identifier}"
            title = (entity or {}).get("title", "")
        else:
            entity = chats.get(identifier)
            kind = "supergroup" if (entity or {}).get("megagroup") else "channel"
            reference = _reference("channel", entity, identifier)
            title = (entity or {}).get("title", "")
        username = ""
        if entity and entity.get("username"):
            username = f"@{entity['username']}"
        rows.append((kind, str(title), username, reference))

    if not rows:
        print("گفت‌وگویی پیدا نشد.")
        return 0
    widths = [max(len(row[index]) for row in rows + [("KIND", "TITLE", "USERNAME", "REFERENCE")]) for index in range(4)]
    print(f"{'KIND':<{widths[0]}}  {'TITLE':<{widths[1]}}  {'USERNAME':<{widths[2]}}  REFERENCE")
    for kind, title, username, reference in rows:
        print(f"{kind:<{widths[0]}}  {title:<{widths[1]}}  {username:<{widths[2]}}  {reference}")
    return 0


def _reference(kind: str, entity: dict[str, Any] | None, identifier: Any) -> str:
    if entity and isinstance(entity.get("access_hash"), int):
        return f"{kind}:{identifier}:{entity['access_hash']}"
    return f"{kind}:{identifier}"


def _display_name(entity: dict[str, Any] | None) -> str:
    if not entity:
        return ""
    name = " ".join(
        part for part in (entity.get("first_name") or "", entity.get("last_name") or "") if part
    ).strip()
    return name or entity.get("username") or ""


async def _run_export(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 1:
        print("--limit باید مثبت باشد.", file=sys.stderr)
        return 2
    if args.page_size < 1:
        print("--page-size باید مثبت باشد.", file=sys.stderr)
        return 2
    try:
        tz = ZoneInfo(args.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        print(f"منطقهٔ زمانی نامعتبر است: {args.timezone}", file=sys.stderr)
        return 2
    try:
        since = _parse_moment(args.since, tz)
        until = _parse_moment(args.until, tz, end_of_day=True)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if since and until and since > until:
        print("--since باید پیش از --until باشد.", file=sys.stderr)
        return 2

    output = Path(args.output).expanduser() if args.output else _default_output(args.peer)
    if output.exists() and not output.is_dir():
        print(f"مسیر خروجی یک پوشه نیست: {output}", file=sys.stderr)
        return 2
    options = ExportOptions(
        output=output,
        include_media=not args.no_media,
        include_stickers=not args.no_stickers,
        limit=args.limit,
        since=since,
        until=until,
        timezone=args.timezone,
        include_created=not args.no_created,
        join_invite=args.join,
        quiet=args.quiet,
        page_size=args.page_size,
    )
    client = await EitaaClient.create(require_auth=True, **_client_options(args))
    async with client:
        exporter = Exporter(client, options)
        stats = await exporter.export(args.peer)
    _print_stats(stats, output)
    return 0


def _print_stats(stats: ExportStats, output: Path) -> None:
    print()
    print(f"خروجی در «{output.resolve()}» ذخیره شد.")
    print(
        f"پیام‌ها: {stats.messages} | رویدادها: {stats.service_messages} | "
        f"عکس: {stats.photos} | ویدیو: {stats.videos} | "
        f"صوت: {stats.voice_messages} | فایل: {stats.documents} | استیکر: {stats.stickers}"
    )
    if stats.bytes_written:
        print(f"حجم رسانه‌های دانلودشده: {_human_size(stats.bytes_written)}")
    print(f"برای دیدن خروجی: file://{output.resolve() / 'messages.html'}")
    if stats.warnings:
        print(f"هشدارها: {len(stats.warnings)} مورد (جزئیات هنگام اجرا نمایش داده شد)")


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _dispatch(argv: Sequence[str]) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        raise SystemExit(0)
    return args


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and not arguments[0].startswith("-") and arguments[0] not in COMMANDS:
        arguments.insert(0, "export")
    args = _dispatch(arguments)
    try:
        if args.command == "version":
            print(f"eitaa-export {__version__}")
            return 0
        if args.command == "login":
            return asyncio.run(_run_login(args))
        if args.command == "chats":
            return asyncio.run(_run_chats(args))
        if args.command == "export":
            return asyncio.run(_run_export(args))
        _build_parser().print_help()
        return 0
    except KeyboardInterrupt:
        print("\nلغو شد.", file=sys.stderr)
        return 130
    except AuthenticationRequired:
        print(
            "ابتدا وارد شوید:  eitaa-export login +989121234567",
            file=sys.stderr,
        )
        return 1
    except OtpError as exc:
        reason = getattr(exc.reason, "value", exc.reason)
        print(f"خطای ورود: {reason}", file=sys.stderr)
        if exc.retry_after_seconds:
            print(f"لطفاً {exc.retry_after_seconds} ثانیه بعد دوباره تلاش کنید.", file=sys.stderr)
        return 1
    except PeerResolutionError as exc:
        print(f"طرف مقابل پیدا نشد: {exc}", file=sys.stderr)
        print("می‌توانید با دستور «eitaa-export chats» شناسهٔ درست را ببینید.", file=sys.stderr)
        return 1
    except EitaaRPCError as exc:
        print(f"خطای سرور ایتا: {exc.code} {exc.text}".rstrip(), file=sys.stderr)
        if exc.retry_after_seconds:
            print(f"لطفاً {exc.retry_after_seconds} ثانیه بعد دوباره تلاش کنید.", file=sys.stderr)
        return 1
    except EitaaError as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"خطا: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
