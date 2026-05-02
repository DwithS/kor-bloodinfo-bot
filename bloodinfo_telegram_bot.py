import asyncio
import datetime
import logging
import os
from typing import Final

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bloodinfo_worker import Worker
from database import (
    add_subscription,
    cancel_subscription,
    get_active_subscriptions,
    get_site_cache_refresh_date,
    get_user_credentials,
    init_db,
    list_subscriptions,
    mark_subscription_notified,
    refresh_sites_cache,
    resolve_site_codes_by_names,
    search_sites_by_region,
    upsert_user_credentials,
)
from encryption_utils import EncryptionError, decrypt_text, encrypt_text, get_env_passphrase

logger = logging.getLogger(__name__)

DONATION_LABEL_TO_KEY: Final[dict[str, str]] = {
    "전혈": "whole_blood",
    "whole_blood": "whole_blood",
    "whole": "whole_blood",
    "wb": "whole_blood",
    "혈장": "plasma",
    "plasma": "plasma",
    "혈소판": "platelet",
    "혈소판혈장": "platelet",
    "platelet": "platelet",
    "plt": "platelet",
    "platelet_plasma": "platelet",
}

DONATION_KEY_TO_LABEL: Final[dict[str, str]] = {
    "whole_blood": "전혈",
    "plasma": "혈장",
    "platelet": "혈소판/혈소판혈장",
}


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "헌혈 예약 알림 봇입니다.\n\n"
        "1) 계정 등록 / 변경\n"
        "/register_account bloodinfo_id bloodinfo_password\n"
        "※ 이미 등록된 경우 새 정보로 덮어씌워집니다.\n\n"
        "2) 알림 조건 등록\n"
        "/add_subscription YYYY-MM-DD donation_types_csv site_names_csv\n"
        "예: /add_subscription 2026-05-10 혈소판,혈장 해운대센터 헌혈의집,서면로센터\n"
        "(헌혈방식: 전혈 / 혈장 / 혈소판 혹은 혈소판혈장)\n\n"
        "3) 등록 확인\n"
        "/list_subscriptions\n\n"
        "4) 등록 취소\n"
        "/cancel_subscription subscription_id\n\n"
        "5) 사이트 조회(시/도 필수, 키워드 선택)\n"
        "/sites 부산\n"
        "/sites 경기 산본"
    )
    await update.message.reply_text(text)


async def cmd_register_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    if len(context.args) != 2:
        await update.message.reply_text("사용법: /register_account bloodinfo_id bloodinfo_password")
        return

    bloodinfo_id, bloodinfo_password = context.args
    passphrase = context.bot_data["enc_passphrase"]
    db_path = context.bot_data["db_path"]

    id_enc = encrypt_text(bloodinfo_id, passphrase)
    pw_enc = encrypt_text(bloodinfo_password, passphrase)

    existing = get_user_credentials(db_path, update.effective_user.id)
    upsert_user_credentials(db_path, update.effective_user.id, id_enc, pw_enc)
    if existing is None:
        await update.message.reply_text("계정이 등록되었습니다.")
    else:
        await update.message.reply_text("계정 정보가 변경되었습니다.")


async def cmd_add_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    if len(context.args) != 3:
        await update.message.reply_text(
            "사용법: /add_subscription YYYY-MM-DD donation_types_csv site_names_csv\n"
            "예: /add_subscription 2026-05-10 혈소판,혈장 해운대센터 헌혈의집,서면로센터"
        )
        return

    credentials = get_user_credentials(context.bot_data["db_path"], update.effective_user.id)
    if credentials is None:
        await update.message.reply_text("먼저 /register_account 로 계정을 등록해주세요.")
        return

    date_text, donation_text, site_text = context.args

    try:
        parsed_date = datetime.date.fromisoformat(date_text)
    except ValueError:
        await update.message.reply_text("날짜 형식이 잘못되었습니다. YYYY-MM-DD 형식으로 입력해주세요.")
        return

    if parsed_date <= datetime.date.today():
        await update.message.reply_text("내일 이후 날짜만 등록할 수 있습니다.")
        return

    donation_types = _parse_donation_types(donation_text)
    if not donation_types:
        await update.message.reply_text("헌혈 방식이 잘못되었습니다. 전혈,혈장,혈소판 중에서 선택해주세요.")
        return

    site_names = _parse_site_names(site_text)
    if not site_names:
        await update.message.reply_text("사이트 이름이 잘못되었습니다. 예: 해운대센터 헌혈의집,서면로센터")
        return

    await _ensure_sites_cache(context.bot_data["db_path"])
    site_codes, not_found = resolve_site_codes_by_names(context.bot_data["db_path"], site_names)
    if not_found:
        await update.message.reply_text(
            "다음 사이트명을 찾지 못했습니다: "
            + ", ".join(not_found)
            + "\n/sites 시/도 [키워드] 로 검색 후 정확한 이름으로 입력해주세요."
        )
        return

    subscription_id = add_subscription(
        context.bot_data["db_path"],
        update.effective_user.id,
        parsed_date.isoformat(),
        donation_types,
        site_codes,
    )

    donation_labels = ",".join(DONATION_KEY_TO_LABEL[item] for item in donation_types)
    await update.message.reply_text(
        f"등록 완료: #{subscription_id}\n"
        f"날짜: {parsed_date.isoformat()}\n"
        f"헌혈방식: {donation_labels}\n"
        f"사이트: {', '.join(site_names)}"
    )


async def cmd_list_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    rows = list_subscriptions(context.bot_data["db_path"], update.effective_user.id)
    if not rows:
        await update.message.reply_text("등록된 알림이 없습니다.")
        return

    lines = []
    for row in rows:
        donation_labels = ",".join(DONATION_KEY_TO_LABEL.get(item, item) for item in row["donation_types"])
        status = "활성" if row["is_active"] else "비활성"
        lines.append(
            f"#{row['id']} | {status} | {row['target_date']} | {donation_labels} | sites={','.join(row['site_codes'])}"
        )

    await update.message.reply_text("\n".join(lines))


async def cmd_cancel_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("사용법: /cancel_subscription subscription_id")
        return

    subscription_id = int(context.args[0])
    success = cancel_subscription(context.bot_data["db_path"], update.effective_user.id, subscription_id)
    if success:
        await update.message.reply_text(f"구독 #{subscription_id} 을(를) 취소했습니다.")
    else:
        await update.message.reply_text("해당 구독을 찾을 수 없거나 이미 비활성 상태입니다.")


async def cmd_sites(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    if len(context.args) < 1:
        await update.message.reply_text("사용법: /sites 시/도 [이름키워드]\n예: /sites 부산\n예: /sites 경기 산본")
        return

    region = context.args[0]
    name_keyword = " ".join(context.args[1:]).strip()

    refreshed = await _ensure_sites_cache(context.bot_data["db_path"])
    if refreshed:
        await update.message.reply_text("사이트 목록 캐시를 갱신했습니다.")

    sites = search_sites_by_region(context.bot_data["db_path"], region, name_keyword, limit=120)

    if not sites:
        await update.message.reply_text("조건에 맞는 사이트를 찾지 못했습니다. 시/도 또는 키워드를 바꿔서 다시 시도해주세요.")
        return

    lines = []
    for site in sites:
        code = site.get("sitecode", "")
        name = site.get("sitename", "")
        orgname = site.get("orgname", "")
        lines.append(f"{name} | {code} | {orgname}")

    await _send_lines_in_chunks(update, lines)


async def cmd_check_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return

    count = await run_polling_iteration(context.bot, context.bot_data["db_path"], context.bot_data["enc_passphrase"], target_user_id=update.effective_user.id)
    await update.message.reply_text(f"검사 완료. 알림 발송 건수: {count}")


def _parse_donation_types(raw_value: str) -> list[str]:
    result: list[str] = []
    for item in raw_value.split(","):
        normalized = item.strip().lower()
        mapped = DONATION_LABEL_TO_KEY.get(normalized)
        if mapped and mapped not in result:
            result.append(mapped)
    return result


def _parse_site_codes(raw_value: str) -> list[str]:
    result: list[str] = []
    for item in raw_value.split(","):
        code = item.strip()
        if code.isdigit() and code not in result:
            result.append(code)
    return result


def _parse_site_names(raw_value: str) -> list[str]:
    result: list[str] = []
    for item in raw_value.split(","):
        name = item.strip()
        if name and name not in result:
            result.append(name)
    return result


def _fetch_sites_from_api() -> list[dict]:
    worker = Worker()
    try:
        return worker.get_sites_list()
    finally:
        worker.close()


async def _ensure_sites_cache(db_path: str) -> bool:
    today = datetime.date.today().isoformat()
    last_refresh_date = get_site_cache_refresh_date(db_path)
    if last_refresh_date == today:
        return False

    sites = await asyncio.to_thread(_fetch_sites_from_api)
    if sites:
        refresh_sites_cache(db_path, sites, today)
        logger.info("사이트 캐시 갱신 완료: %s건", len(sites))
        return True

    logger.warning("사이트 캐시 갱신 실패: API 응답이 비어있습니다")
    return False


async def _send_lines_in_chunks(update: Update, lines: list[str], max_chars: int = 3500) -> None:
    if update.message is None:
        return

    chunk: list[str] = []
    current_len = 0
    for line in lines:
        plus_len = len(line) + 1
        if chunk and current_len + plus_len > max_chars:
            await update.message.reply_text("\n".join(chunk))
            chunk = [line]
            current_len = plus_len
        else:
            chunk.append(line)
            current_len += plus_len

    if chunk:
        await update.message.reply_text("\n".join(chunk))


def _build_notify_message(subscription_id: int, target_date: str, matches: list[tuple[str, list[str]]], donation_types: list[str]) -> str:
    donation_labels = ", ".join(DONATION_KEY_TO_LABEL.get(item, item) for item in donation_types)
    lines = [
        "조건에 맞는 예약 가능 슬롯을 찾았습니다.",
        f"구독 ID: #{subscription_id}",
        f"날짜: {target_date}",
        f"헌혈방식: {donation_labels}",
        "",
    ]

    for site_code, times in matches:
        lines.append(f"- site {site_code}: {', '.join(times)}")

    lines.append("")
    lines.append("해당 구독은 1회 알림 후 자동 비활성화되었습니다. 다시 알림받으려면 새로 등록해주세요.")
    return "\n".join(lines)


def _check_one_subscription(sub: dict, passphrase: str) -> tuple[bool, str]:
    user_id = decrypt_text(sub["bloodinfo_id_enc"], passphrase)
    user_password = decrypt_text(sub["bloodinfo_pw_enc"], passphrase)
    target_date = datetime.date.fromisoformat(sub["target_date"])
    donation_types = sub["donation_types"]
    site_codes = sub["site_codes"]

    worker = Worker()
    try:
        if not worker.login(user_id, user_password):
            return False, ""

        matches: list[tuple[str, list[str]]] = []
        for site_code in site_codes:
            table = worker.fetch_time_table(site_code, target_date)
            if not table:
                continue
            times = worker.find_available_slots(table, donation_types)
            if times:
                matches.append((site_code, times))

        if not matches:
            return False, ""

        message = _build_notify_message(sub["id"], sub["target_date"], matches, donation_types)
        return True, message
    finally:
        worker.close()


async def run_polling_iteration(bot, db_path: str, passphrase: str, target_user_id: int | None = None) -> int:
    subscriptions = get_active_subscriptions(db_path)
    if target_user_id is not None:
        subscriptions = [sub for sub in subscriptions if sub["telegram_user_id"] == target_user_id]

    notified_count = 0
    today = datetime.date.today()
    for sub in subscriptions:
        try:
            if datetime.date.fromisoformat(sub["target_date"]) <= today:
                mark_subscription_notified(db_path, sub["id"])
                logger.info("구독 #%s 대상 날짜 만료로 비활성화", sub["id"])
                continue
            matched, message = await asyncio.to_thread(_check_one_subscription, sub, passphrase)
            if not matched:
                continue
            await bot.send_message(chat_id=sub["telegram_user_id"], text=message)
            mark_subscription_notified(db_path, sub["id"])
            notified_count += 1
        except EncryptionError:
            logger.exception("구독 #%s 복호화 실패", sub["id"])
        except Exception:
            logger.exception("구독 #%s 검사 중 오류", sub["id"])
    return notified_count


async def polling_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    count = await run_polling_iteration(context.bot, context.bot_data["db_path"], context.bot_data["enc_passphrase"])
    logger.info("주기 검사 완료, 알림 %s건 발송", count)


def main() -> None:
    load_dotenv()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

    db_path = os.getenv("DB_PATH", "telegram_bot.sqlite3").strip()
    poll_interval_minutes = int(os.getenv("POLL_INTERVAL_MINUTES", "30"))
    passphrase = get_env_passphrase()

    init_db(db_path)

    application = Application.builder().token(token).build()
    application.bot_data["db_path"] = db_path
    application.bot_data["enc_passphrase"] = passphrase

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("register_account", cmd_register_account))
    application.add_handler(CommandHandler("add_subscription", cmd_add_subscription))
    application.add_handler(CommandHandler("list_subscriptions", cmd_list_subscriptions))
    application.add_handler(CommandHandler("cancel_subscription", cmd_cancel_subscription))
    application.add_handler(CommandHandler("sites", cmd_sites))
    application.add_handler(CommandHandler("check_now", cmd_check_now))

    if application.job_queue is None:
        raise RuntimeError("JobQueue is not enabled. Install python-telegram-bot[job-queue].")

    application.job_queue.run_repeating(
        polling_job,
        interval=datetime.timedelta(minutes=poll_interval_minutes),
        first=datetime.timedelta(seconds=15),
        name="bloodinfo-check-job",
    )

    logger.info("텔레그램 봇 시작")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    main()
