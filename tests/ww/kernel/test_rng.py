from werewolf.kernel.rng import Rng


def test_same_seed_same_stream() -> None:
    a, b = Rng(7), Rng(7)
    assert [a.random() for _ in range(5)] == [b.random() for _ in range(5)]
    assert Rng(7).random() != Rng(8).random()


def test_fork_is_independent_of_parent_draws() -> None:
    a = Rng(7)
    first = a.fork('mind:3').random()
    for _ in range(10):
        a.random()
    assert a.fork('mind:3').random() == first
    assert a.fork('mind:4').random() != first
    assert a.fork('x').fork('y').random() == Rng(7, 'x/y').random()


def test_state_roundtrip_and_weighted() -> None:
    a = Rng(1, 'k')
    a.random()
    b = Rng.from_state(a.state())
    assert [a.randint(1, 100) for _ in range(5)] == [b.randint(1, 100) for _ in range(5)]
    r = Rng(3)
    picks = [r.weighted({'x': 1.0, 'y': 0.0, 'z': 3.0}) for _ in range(200)]
    assert 'y' not in picks and picks.count('z') > picks.count('x')
