from fastapi.testclient import TestClient

from app.main import create_app


def test_openapi_exposes_the_response_schemas() -> None:
    client = TestClient(create_app())

    schema = client.get("/openapi.json").json()

    schemas = schema["components"]["schemas"]
    assert "ProductSearchResponse" in schemas
    assert "Product" in schemas
    assert "SearchMetadata" in schemas


def test_openapi_exposes_the_products_route_with_its_parameters() -> None:
    client = TestClient(create_app())

    schema = client.get("/openapi.json").json()

    operation = schema["paths"]["/api/v1/products"]["get"]
    param_names = {p["name"] for p in operation["parameters"]}
    assert {"postal_code", "term"} <= param_names
