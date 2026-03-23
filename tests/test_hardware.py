from localsmartz.utils.hardware import get_ram_gb


def test_get_ram_gb_returns_positive_int():
    """get_ram_gb returns a positive integer on this machine."""
    ram = get_ram_gb()
    assert isinstance(ram, int)
    assert ram > 0
