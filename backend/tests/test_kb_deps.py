def test_reme_importable():
    import reme

    assert reme.__version__ == "0.4.1.8"
    assert hasattr(reme, "Application")
