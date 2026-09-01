import re


class PhoneNormalizationError(ValueError):
    pass


class PhoneNormalizationService:
    @staticmethod
    def normalize(value: str) -> str:
        digits = re.sub(r"\D", "", value or "")
        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        if not 10 <= len(digits) <= 15:
            raise PhoneNormalizationError("Некорректный номер телефона")
        return "+" + digits

    @staticmethod
    def mask(value: str) -> str:
        return value[:3] + "***" + value[-2:] if len(value) > 6 else "***"
