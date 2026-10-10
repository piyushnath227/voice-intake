import json
from .conftest import GOOD

def test_create_get_list_filter(client):
    r = client.post("/patients", json=GOOD)
    assert r.status_code == 201 and r.json()["error"] is None
    p = r.json()["data"]
    assert p["last_name"] == "O'Brien" and p["state"] == "NY" and p["phone_number"] == "2125550142"
    assert client.get(f"/patients/{p['patient_id']}").json()["data"]["first_name"] == "Jane"
    assert len(client.get("/patients?last_name=o'brien").json()["data"]) == 1
    assert len(client.get("/patients?phone_number=212-555-0142").json()["data"]) == 1
    assert len(client.get("/patients?date_of_birth=04/12/1990").json()["data"]) == 1
    assert client.get("/patients?last_name=nobody").json()["data"] == []

def test_validation_422_names_the_field(client):
    r = client.post("/patients", json={**GOOD, "phone_number": "123"})
    body = r.json()
    assert r.status_code == 422 and body["data"] is None
    assert body["error"]["fields"][0]["field"] == "phone_number"
    assert "3 digits" in body["error"]["fields"][0]["message"]

def test_future_dob_and_missing_fields(client):
    assert client.post("/patients", json={**GOOD, "date_of_birth": "01/01/2999"}).status_code == 422
    r = client.post("/patients", json={"first_name": "Jane"})
    assert r.status_code == 422 and len(r.json()["error"]["fields"]) >= 5

def test_bad_json_400_and_bad_uuid_400_and_404(client):
    assert client.post("/patients", content="{nope", headers={"content-type": "application/json"}).status_code == 400
    assert client.get("/patients/not-a-uuid").status_code == 400
    assert client.get("/patients/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/nope").json()["error"]["code"] == "not_found"

def test_duplicate_phone_409(client):
    client.post("/patients", json=GOOD)
    r = client.post("/patients", json={**GOOD, "first_name": "Other"})
    assert r.status_code == 409 and r.json()["error"]["existing_patient_id"]

def test_partial_update_and_readonly_fields(client):
    pid = client.post("/patients", json=GOOD).json()["data"]["patient_id"]
    r = client.put(f"/patients/{pid}", json={"city": "Brooklyn", "email": "j@x.com"})
    assert r.status_code == 200 and r.json()["data"]["city"] == "Brooklyn" and r.json()["data"]["first_name"] == "Jane"
    assert client.put(f"/patients/{pid}", json={"patient_id": "x"}).status_code == 422
    assert client.put(f"/patients/{pid}", json={}).status_code == 400
    assert client.put(f"/patients/{pid}", json={"zip_code": "12"}).status_code == 422

def test_soft_delete(client, fake):
    pid = client.post("/patients", json=GOOD).json()["data"]["patient_id"]
    assert client.delete(f"/patients/{pid}").status_code == 200
    assert client.get(f"/patients/{pid}").status_code == 404
    assert pid in fake.rows                      # row still exists (soft delete)
    assert client.delete(f"/patients/{pid}").status_code == 404

def test_db_outage_gives_500_envelope_not_a_crash(client, fake):
    fake.fail = True
    r = client.get("/patients")
    assert r.status_code == 500 and r.json()["error"]["code"] == "database_error"

def test_dashboard_and_health(client):
    assert "Patient Registry" in client.get("/").text
    assert client.get("/health").json()["data"]["status"] == "ok"

# ---------- voice webhook ----------
def tool(client, name, args, headers=None):
    body = {"message": {"type": "tool-calls", "call": {"id": "call_1", "customer": {"number": "+12125550199"}},
                        "toolCallList": [{"id": "tc1", "name": name, "arguments": args}]}}
    r = client.post("/vapi/webhook", json=body, headers=headers or {})
    return r, (json.loads(r.json()["results"][0]["result"]) if r.status_code == 200 else None)

def test_voice_validate_field_reprompts_specific_field(client):
    _, res = tool(client, "validate_field", {"field": "phone_number", "value": "555"})
    assert res["valid"] is False and "3 digits" in res["problem"]
    _, res = tool(client, "validate_field", {"field": "date_of_birth", "value": "01/01/2999"})
    assert res["valid"] is False and "future" in res["problem"]
    _, res = tool(client, "validate_field", {"field": "state", "value": "texas"})
    assert res == {"valid": True, "field": "state", "normalized": "TX"}

def test_voice_register_then_duplicate_then_update(client):
    _, res = tool(client, "register_patient", {**GOOD, "address_line_2": "", "email": None})
    assert res["success"] and res["first_name"] == "Jane"
    pid = res["patient_id"]
    _, res = tool(client, "find_patient_by_phone", {"phone_number": "212 555 0142"})
    assert res["found"] and res["first_name"] == "Jane"
    _, res = tool(client, "register_patient", GOOD)
    assert res["duplicate"] and res["existing_first_name"] == "Jane"
    _, res = tool(client, "update_patient", {"patient_id": pid, "verify_date_of_birth": "01/01/1980", "city": "Queens"})
    assert res["verification_failed"]
    _, res = tool(client, "update_patient", {"patient_id": pid, "verify_date_of_birth": "04/12/1990", "city": "Queens"})
    assert res["success"] and client.get(f"/patients/{pid}").json()["data"]["city"] == "Queens"

def test_voice_register_invalid_lists_fields(client):
    _, res = tool(client, "register_patient", {**GOOD, "zip_code": "12", "state": "Narnia"})
    assert not res["success"] and {e["field"] for e in res["errors"]} == {"zip_code", "state"}

def test_voice_db_failure_gives_spoken_fallback_not_silence(client, fake):
    fake.fail = True
    _, res = tool(client, "register_patient", GOOD)
    assert res["system_error"] and "haven't been able to save" in res["say"]

def test_webhook_secret_enforced(client, monkeypatch):
    monkeypatch.setenv("VAPI_SECRET", "s3cret")
    assert tool(client, "validate_field", {"field": "sex", "value": "m"})[0].status_code == 401
    r, res = tool(client, "validate_field", {"field": "sex", "value": "m"}, {"X-Vapi-Secret": "s3cret"})
    assert r.status_code == 200 and res["normalized"] == "Male"

def test_end_of_call_report_saved(client, fake):
    r = client.post("/vapi/webhook", json={"message": {"type": "end-of-call-report", "endedReason": "customer-ended-call",
        "call": {"id": "call_9"}, "summary": "Registered Jane", "artifact": {"transcript": "AI: Hi"}}})
    assert r.status_code == 200 and fake.saved[0] == "call_9" and fake.saved[-1] == "AI: Hi"
