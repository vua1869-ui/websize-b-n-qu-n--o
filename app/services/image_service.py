import logging
from typing import Any, BinaryIO, Dict, Optional, Union
import cloudinary
import cloudinary.uploader

from app.config import settings

logger = logging.getLogger(__name__)


class ImageService:
    def __init__(self):
        self._configured = False
        self._ensure_config()

    def _ensure_config(self):
        if settings.CLOUDINARY_CLOUD_NAME and settings.CLOUDINARY_API_KEY and settings.CLOUDINARY_API_SECRET:
            cloudinary.config(
                cloud_name=settings.CLOUDINARY_CLOUD_NAME,
                api_key=settings.CLOUDINARY_API_KEY,
                api_secret=settings.CLOUDINARY_API_SECRET,
                secure=True,
            )
            self._configured = True
        else:
            logger.warning("Cloudinary credentials are missing or incomplete in settings.")
            self._configured = False

    def upload_image(
        self,
        file: Union[BinaryIO, bytes, str],
        folder: str = "aura_store",
        public_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Tải ảnh lên Cloudinary với chế độ tự động tối ưu định dạng và dung lượng (f_auto, q_auto).
        Trả về dictionary chứa 'url' (secure_url) và 'public_id'.
        """
        self._ensure_config()
        options: Dict[str, Any] = {
            "folder": folder,
            "resource_type": "image",
            "transformation": [
                {"quality": "auto", "fetch_format": "auto"}
            ],
        }
        if public_id:
            options["public_id"] = public_id

        upload_result = cloudinary.uploader.upload(file, **options)
        return {
            "url": upload_result.get("secure_url") or upload_result.get("url"),
            "public_id": upload_result.get("public_id"),
            "format": upload_result.get("format"),
            "width": upload_result.get("width"),
            "height": upload_result.get("height"),
            "bytes": upload_result.get("bytes"),
        }

    def delete_image(self, public_id: str) -> bool:
        """
        Xóa ảnh khỏi Cloudinary thông qua public_id.
        """
        if not public_id:
            return False
        self._ensure_config()
        try:
            res = cloudinary.uploader.destroy(public_id)
            return res.get("result") in ["ok", "not found"]
        except Exception as e:
            logger.error(f"Lỗi khi xóa ảnh Cloudinary public_id={public_id}: {e}")
            return False


image_service = ImageService()
