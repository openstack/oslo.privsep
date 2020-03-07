import os.path


def load_tests(loader, tests, pattern):
    this_dir = os.path.dirname(__file__)
    new_tests = loader.discover(start_dir=this_dir, pattern=pattern)
    tests.addTests(new_tests)
    return tests
