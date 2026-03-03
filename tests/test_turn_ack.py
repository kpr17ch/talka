from app.config import Settings
from app.turn_ack import build_turn_ack_text


def test_ack_auto_skips_short_simple_question():
    settings = Settings(TURN_ACK_MODE="auto")
    text = build_turn_ack_text(user_text="Wie spaet ist es?", settings=settings)
    assert text == ""


def test_ack_auto_for_long_task_request():
    settings = Settings(TURN_ACK_MODE="auto")
    text = build_turn_ack_text(
        user_text="Bitte analysiere den Fehler im Backend, erstelle einen detaillierten Bericht und gib mir danach die naechsten Schritte.",
        settings=settings,
    )
    assert "kuemmere" in text


def test_ack_off_disables_confirmation():
    settings = Settings(TURN_ACK_MODE="off")
    text = build_turn_ack_text(user_text="Bitte analysiere den Fehler und dokumentiere alles.", settings=settings)
    assert text == ""


def test_ack_always_forces_confirmation():
    settings = Settings(TURN_ACK_MODE="always")
    text = build_turn_ack_text(user_text="Hi", settings=settings)
    assert "kuemmere" in text


def test_ack_custom_text_is_used():
    settings = Settings(
        TURN_ACK_MODE="always",
        TURN_ACK_TEXT="Alles klar, ich uebernehme das und melde mich gleich.",
    )
    text = build_turn_ack_text(user_text="Kannst du das pruefen?", settings=settings)
    assert text == "Alles klar, ich uebernehme das und melde mich gleich."
