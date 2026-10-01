from src.models import ModelInfo
from src.router import CATEGORIES, _category_to_model, build_category_map


def test_categories_defined():
    assert "coding" in CATEGORIES
    assert "reasoning" in CATEGORIES
    assert "writing" in CATEGORIES
    assert "chat" in CATEGORIES
    assert "analysis" in CATEGORIES
    assert "creative" in CATEGORIES


def test_build_category_map():
    models = [
        ModelInfo(
            id="openrouter/deepseek/deepseek-v3.1",
            discount_pct=50,
            provider="o",
            input_price=0.0000001,
            output_price=0.0000005,
            context_length=131072,
        ),
        ModelInfo(
            id="openrouter/google/gemini-2.0-flash",
            discount_pct=75,
            provider="o",
            input_price=0.000000075,
            output_price=0.0000003,
            context_length=1048576,
        ),
        ModelInfo(
            id="openrouter/anthropic/claude-3.5-sonnet",
            discount_pct=50,
            provider="o",
            input_price=0.000003,
            output_price=0.000015,
            context_length=200000,
        ),
    ]
    result = build_category_map(models)
    assert isinstance(result, dict)
    assert len(result) == 6


def test_category_to_model_direct():
    build_category_map(
        [
            ModelInfo(
                id="openrouter/deepseek/deepseek-v3.1",
                discount_pct=50,
                provider="o",
                input_price=0.0000001,
                output_price=0.0000005,
                context_length=131072,
            ),
        ]
    )
    result = _category_to_model("coding")
    assert result == "openrouter/deepseek/deepseek-v3.1"


def test_category_to_model_fallback():
    build_category_map(
        [
            ModelInfo(
                id="openrouter/google/gemini-2.0-flash",
                discount_pct=75,
                provider="o",
                input_price=0.000000075,
                output_price=0.0000003,
                context_length=1048576,
            ),
        ]
    )
    result = _category_to_model("unknown_category")
    assert result is not None
