import re

from flashdrop.store import OrderError


def validate_order(body):
    fields = {"userId", "productId", "quantity", "idempotencyKey"}
    if not isinstance(body, dict) or set(body) != fields:
        raise OrderError(
            422, "VALIDATION_ERROR", "Provide exactly userId, productId, quantity, and idempotencyKey."
        )
    for field in ("userId", "productId", "idempotencyKey"):
        value = body[field]
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
            raise OrderError(
                422,
                "VALIDATION_ERROR",
                f"{field} must contain 1-128 letters, digits, dots, colons, underscores, or hyphens.",
            )
    if type(body["quantity"]) is not int or not 1 <= body["quantity"] <= 10000:
        raise OrderError(422, "VALIDATION_ERROR", "quantity must be an integer between 1 and 10000.")
    return body
