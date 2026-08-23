from pathlib import Path

from aiogram.types import FSInputFile


ASSET_PREFIX = "asset:"


def resolve_mailing_photo(image_file_id: str | None, assets_dir: Path):
    if not image_file_id:
        return None
    if not image_file_id.startswith(ASSET_PREFIX):
        return image_file_id
    asset_name = Path(image_file_id.removeprefix(ASSET_PREFIX)).name
    asset_path = assets_dir / asset_name
    return FSInputFile(asset_path) if asset_path.is_file() else None

