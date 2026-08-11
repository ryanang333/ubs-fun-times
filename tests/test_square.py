from app import app


def test_square():
    with app.test_client() as client:
        response = client.post("/square", json={"number": 4})

    assert response.status_code == 200
    assert response.get_json() == {"answer": 16}


def test_square_rejects_non_numeric_input():
    with app.test_client() as client:
        response = client.post("/square", json={"number": "4"})

    assert response.status_code == 400
