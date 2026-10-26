"""Órdenes de mezclador en español."""
from aiengineer.nlp.mixer_commands import MixerCommand, parse_mixer


def test_volume_orders():
    assert parse_mixer("pon la pista 1 a -6 dB") == MixerCommand("set_db", "1", -6.0)
    assert parse_mixer("sube la pista 1 2 dB") == MixerCommand("change_db", "1", 2.0)
    assert parse_mixer("baja la pista del bajo 3 dB") == MixerCommand("change_db", "bajo", -3.0)
    assert parse_mixer("pon el kick a -6 dB") == MixerCommand("set_db", "kick", -6.0)


def test_track_word_before_name():
    # «la pista bass 1» busca la pista llamada «bass», no «pista bass»
    cmd = parse_mixer("Sube la pista bass 1 2 dB")
    assert cmd.action == "change_db" and cmd.target == "bass" and cmd.value == 2.0
