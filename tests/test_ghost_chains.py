from app import app


def post_transactions(client, edges, start_minute=0):
    transactions = []
    for index, (source, target) in enumerate(edges):
        transactions.append(
            {
                "txId": f"tx-{start_minute}-{index}-{source}-{target}",
                "fromUserId": source,
                "toUserId": target,
                "amount": 100.0,
                "createdAt": f"2026-06-08T12:{start_minute + index:02d}:00Z",
                "ignoredFutureField": "allowed",
            }
        )
    return client.post("/ghost-chains/transactions", json={"transactions": transactions})


def test_health():
    with app.test_client() as client:
        response = client.get("/ghost-chains/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_reset():
    with app.test_client() as client:
        response = client.post(
            "/ghost-chains/reset", json={"clearTransactions": True}
        )

    assert response.status_code == 200
    assert response.get_json() == {"clearTransactions": True}


def test_transactions_preserve_order_and_allow_optional_fields_to_be_absent():
    payload = {
        "transactions": [
            {
                "txId": "tx_meridian_001",
                "fromUserId": "meridian_holdings",
                "toUserId": "apex_logistics",
                "amount": 370.0,
                "createdAt": "2026-06-08T12:00:00Z",
            },
            {
                "txId": "tx_cascade_014",
                "fromUserId": "cascade_payments",
                "toUserId": "horizon_capital",
                "amount": 100.0,
                "createdAt": "2026-06-08T12:01:00Z",
                "ipAddress": "203.0.113.8",
                "deviceId": "device-1",
            },
        ]
    }

    with app.test_client() as client:
        response = client.post("/ghost-chains/transactions", json=payload)

    assert response.status_code == 200
    assert response.get_json() == {
        "transactions": [
            {"txId": "tx_meridian_001", "riskScore": 0.0},
            {"txId": "tx_cascade_014", "riskScore": 0.0},
        ]
    }


def test_invalid_payload_returns_bad_request():
    with app.test_client() as client:
        response = client.post("/ghost-chains/transactions", json={})

    assert response.status_code == 400


def test_phase_one_structural_ordering():
    examples = [
        [("M", "A")],
        [("M", "A"), ("A", "C")],
        [("M", "A"), ("M", "H"), ("A", "S"), ("H", "S")],
        [("M", "A"), ("A", "C"), ("C", "O"), ("O", "A")],
        [("M", "A"), ("A", "C"), ("C", "M"), ("A", "N"), ("N", "M")],
    ]

    final_scores = []
    with app.test_client() as client:
        for edges in examples:
            client.post("/ghost-chains/reset", json={"clearTransactions": True})
            response = post_transactions(client, edges)
            final_scores.append(response.get_json()["transactions"][-1]["riskScore"])

    assert final_scores == [0.0, 0.15, 0.45, 0.70, 0.95]


def test_duplicate_is_idempotent_and_does_not_mutate_graph():
    with app.test_client() as client:
        client.post("/ghost-chains/reset", json={"clearTransactions": True})
        original = {
            "txId": "same-id",
            "fromUserId": "M",
            "toUserId": "A",
            "amount": 1,
            "createdAt": "2026-06-08T12:00:00Z",
        }
        changed_duplicate = {
            **original,
            "fromUserId": "X",
            "toUserId": "Y",
        }
        first = client.post(
            "/ghost-chains/transactions", json={"transactions": [original]}
        ).get_json()
        duplicate = client.post(
            "/ghost-chains/transactions", json={"transactions": [changed_duplicate]}
        ).get_json()
        extension = post_transactions(client, [("A", "C")], start_minute=1).get_json()

    assert duplicate == first
    assert extension["transactions"][0]["riskScore"] == 0.15


def test_transactions_outside_24_hour_window_are_evicted():
    with app.test_client() as client:
        client.post("/ghost-chains/reset", json={"clearTransactions": True})
        old = {
            "txId": "old",
            "fromUserId": "M",
            "toUserId": "A",
            "amount": 1,
            "createdAt": "2026-06-07T12:00:00Z",
        }
        new = {
            "txId": "new",
            "fromUserId": "A",
            "toUserId": "C",
            "amount": 1,
            "createdAt": "2026-06-08T12:00:01Z",
        }
        response = client.post(
            "/ghost-chains/transactions", json={"transactions": [old, new]}
        )

    assert response.get_json()["transactions"][-1]["riskScore"] == 0.0


def test_shared_identity_across_disconnected_components_adds_coordination_signal():
    with app.test_client() as client:
        client.post("/ghost-chains/reset", json={"clearTransactions": True})
        transactions = [
            {
                "txId": f"identity-{index}",
                "fromUserId": source,
                "toUserId": target,
                "amount": 1,
                "createdAt": f"2026-06-08T12:0{index}:00Z",
                "ipAddress": "10.0.0.1",
            }
            for index, (source, target) in enumerate(
                [("M", "A"), ("C", "H"), ("O", "S")]
            )
        ]
        response = client.post(
            "/ghost-chains/transactions", json={"transactions": transactions}
        )

    scores = [item["riskScore"] for item in response.get_json()["transactions"]]
    assert scores == [0.0, 0.06, 0.12]


def test_ip_and_device_are_independent_identity_dimensions():
    with app.test_client() as client:
        client.post("/ghost-chains/reset", json={"clearTransactions": True})
        common = {
            "amount": 1,
            "ipAddress": "10.0.0.1",
            "deviceId": "shared-device",
        }
        transactions = [
            {
                **common,
                "txId": "both-1",
                "fromUserId": "M",
                "toUserId": "A",
                "createdAt": "2026-06-08T12:00:00Z",
            },
            {
                **common,
                "txId": "both-2",
                "fromUserId": "C",
                "toUserId": "H",
                "createdAt": "2026-06-08T12:01:00Z",
            },
        ]
        response = client.post(
            "/ghost-chains/transactions", json={"transactions": transactions}
        )

    assert response.get_json()["transactions"][-1]["riskScore"] == 0.12


def test_identity_alignment_strengthens_a_return():
    with app.test_client() as client:
        client.post("/ghost-chains/reset", json={"clearTransactions": True})
        transactions = [
            {
                "txId": f"return-{index}",
                "fromUserId": source,
                "toUserId": target,
                "amount": 1,
                "createdAt": f"2026-06-08T12:0{index}:00Z",
                "deviceId": "same-device",
            }
            for index, (source, target) in enumerate(
                [("M", "A"), ("A", "C"), ("C", "M")]
            )
        ]
        response = client.post(
            "/ghost-chains/transactions", json={"transactions": transactions}
        )

    assert response.get_json()["transactions"][-1]["riskScore"] == 0.78
