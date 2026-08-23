from aiogram.types import FSInputFile

from app.bot.media import resolve_mailing_photo


def test_local_mailing_asset_is_resolved_safely(tmp_path):
    image = tmp_path / "promo.jpg"
    image.write_bytes(b"image")

    resolved = resolve_mailing_photo("asset:../promo.jpg", tmp_path)

    assert isinstance(resolved, FSInputFile)
    assert resolved.path == image


def test_telegram_file_id_is_kept_unchanged(tmp_path):
    assert resolve_mailing_photo("telegram-file-id", tmp_path) == "telegram-file-id"
