from calc.arithmetic import add, average, subtract


def test_add():
    assert add(2, 3) == 5


def test_subtract():
    assert subtract(5, 2) == 3


def test_average():
    assert average([2, 4, 6]) == 4
