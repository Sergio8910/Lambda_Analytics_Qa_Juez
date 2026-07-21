"""
Shared pytest fixtures/config for tests.

Las fixtures de base de datos (test_db_engine, sample_tenant, ...) se
eliminaron junto con el subsistema control_plane (peso muerto: roto por
incompatibilidad de sqlalchemy y sin uso en producción). Solo las usaban los
tests de control_plane, que también se removieron.
"""


def pytest_configure(config):
    config.addinivalue_line("markers", "unit: mark test as a unit test")
    config.addinivalue_line("markers", "integration: mark test as an integration test")
