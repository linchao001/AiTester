def test_reme_importable():
    import reme

    assert reme.__version__ == "0.4.1.13"
    assert hasattr(reme, "Application")
