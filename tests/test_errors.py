"""Tests for src.errors.ValidationError."""

from src.errors import ValidationError


def test_validation_error_is_exception():
    assert issubclass(ValidationError, Exception)


def test_validation_error_preserves_message():
    err = ValidationError("observer.lat must be between -90 and 90")
    assert str(err) == "observer.lat must be between -90 and 90"
