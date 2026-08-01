from django.core.exceptions import ValidationError
from PIL import Image

MAX_IMAGE_SIZE = 5 * 1024 * 1024
MAX_IMAGE_DIMENSIONS = 4096
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def validate_image_file(image):
    if image.size > MAX_IMAGE_SIZE:
        raise ValidationError("Image size must not exceed 5MB.")
    try:
        with Image.open(image) as img:
            image_format = img.format
            width, height = img.size
    except Exception:
        raise ValidationError("Uploaded file is not a valid image.") from None
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError("Only JPEG, PNG, and WebP images are allowed.")
    if width > MAX_IMAGE_DIMENSIONS or height > MAX_IMAGE_DIMENSIONS:
        raise ValidationError(
            f"Image dimensions must not exceed {MAX_IMAGE_DIMENSIONS}x{MAX_IMAGE_DIMENSIONS}px."
        )
    image.seek(0)
