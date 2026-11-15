from tianlong import prompting


def test_render_and_load(tmp_path):
    (tmp_path / "jev").mkdir()
    (tmp_path / "a.md").write_text("你好，{{ name }}。\n", encoding="utf-8")
    (tmp_path / "jev" / "b.md").write_text("固定的块\n", encoding="utf-8")
    old = prompting.root()
    prompting.set_root(tmp_path)
    try:
        assert prompting.render("a", name="段公子") == "你好，段公子。"
        assert prompting.load("jev/b") == "固定的块"
        assert prompting.exists("a") and not prompting.exists("c")
    finally:
        prompting.set_root(old)
